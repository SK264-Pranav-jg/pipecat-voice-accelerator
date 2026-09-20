import os
import asyncio
from pathlib import Path
import logging

#pipecat imports
from pipecat.pipeline.pipeline import Pipeline
from pipecat.workers.runner import WorkerRunner
from pipecat.pipeline.worker import PipelineParams , PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair ,
    LLMUserAggregatorParams,
    LLMAssistantAggregatorParams,
    UserTurnStoppedMessage,
    AssistantTurnStoppedMessage,
)
from pipecat.utils.context.llm_context_summarization import (
    LLMAutoContextSummarizationConfig , 
    LLMContextSummaryConfig , 
)
from pipecat.runner.types import RunnerArguments 
from pipecat.runner.utils import create_transport 
from pipecat.transports.base_transport import BaseTransport , TransportParams 
from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport , FastAPIWebsocketParams 
from pipecat.transcriptions.language import Language
from pipecat.frames.frames import LLMMessagesAppendFrame , TTSSpeakFrame , LLMRunFrame

# rnn filter 
from pipecat.audio.filters.rnnoise_filter import RNNoiseFilter

# pipecat-vobiz import 
from pipecat.serializers.vobiz import VobizFrameSerializer , parse_vobiz_start 

# strands agents plugin , (plugin to be used in case of using strands agent instead pipecat native LLMServices) 
from pipecat.processors.frameworks.strands_agents import StrandsAgentsProcessor 

# background ambience imports 
# import to add background ambient sounds to the bot 
from pipecat.audio.mixers.soundfile_mixer import SoundfileMixer 

# aws llm 
from pipecat.services.aws.llm import AWSBedrockLLMService , AWSBedrockLLMSettings 

# eleven labs services import 
from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService , ElevenLabsRealtimeSTTSettings , CommitStrategy 
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService , ElevenLabsTTSSettings 

# cartesia services import 
from pipecat.services.cartesia.stt import CartesiaSTTService , CartesiaSTTSettings 
from pipecat.services.cartesia.tts import CartesiaTTSService , CartesiaTTSSettings  

# sarvam services import 
from pipecat.services.sarvam.stt import SarvamSTTService , SarvamSTTSettings 
from pipecat.services.sarvam.tts import SarvamTTSService , SarvamTTSSettings 

# deepgram services import 
from pipecat.services.deepgram.stt import DeepgramSTTService , DeepgramSTTSettings 
from pipecat.services.deepgram.tts import DeepgramTTSService , DeepgramTTSSettings 

# VAD imports
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams

# turn-taking — SpeechTimeoutUserTurnStopStrategy (silence timeout) instead of the
# framework default LocalSmartTurnAnalyzerV3 (a local ML model run on every turn) to
# keep response latency low and predictable
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy

# latency visibility — enable_metrics=True alone collects nothing you can see
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver

# backend import
from backend.src.pipecat.idle_handler import IdleHandler
from backend.src.pipecat.call_session import CallSession
from backend.src.config.settings import settings
from backend.src.ai.prompts.prompt_main import return_prompt

# tool imports
from backend.src.ai.tools.current import get_current_datetime
from backend.src.ai.tools.end_call import create_end_call_tool

# db imports (call metadata + transcript persistence)
from backend.src.db.repository import create_call, add_message, end_call

logger = logging.getLogger(__name__)

# path of the audio file that is to be used as the background ambience (change the path according to the file location) 
# the sample rate of the file should the same as the transport sample rate 
# BACKEND_DIR = Path(__file__).resolve().parents[1]
# AUDIO_DIR = BACKEND_DIR / "assets" / "resample_output.wav"

# adjust the parameters of your transport layer over here 
transport_params = {
    # SmallWebRTC for browser sessions 
    "webrtc": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        # background noise filter 
        audio_in_filter=RNNoiseFilter(),
        audio_in_sample_rate=16000,
        audio_out_sample_rate=16000,
        # background ambience sound (change the path of the file ) 
        # audio_out_mixer=SoundfileMixer(
        #     sound_files={"office": str(AUDIO_DIR)},
        #     default_sound="office",
        #     loop=True,
        #     volume=2.0,
        # ),
    ),
    # Vobiz telephony — bidirectional 8kHz WebSocket stream
    # serializer is NOT set here — VobizFrameSerializer needs stream_id
    # from the first Vobiz frame (parse_vobiz_start). It is constructed and
    # passed in FastAPIWebsocketTransport() directly inside the /ws handler.
    "vobiz": lambda: FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        add_wav_header=False,
        audio_in_sample_rate=8000,
        audio_out_sample_rate=8000,
        # background noise filter 
        audio_in_filter=RNNoiseFilter(),
        # background ambience sound (change the path of the file ) 
        # audio_out_mixer=SoundfileMixer(
        #     sound_files={"office": str(AUDIO_DIR)},
        #     default_sound="office",
        #     loop=True,
        #     volume=2.0,
        # ),
    ),
}


async def build_pipeline(
    transport ,
    call_id : str ,
    transport_type : str ,
    provider : str | None = None ,
    caller_name : str | None = None ,
    phone_number : str | None = None ,
) -> PipelineWorker:

    # derive sample rate from transport params
    # TransportParams exposes audio_in_sample_rate; FastAPIWebsocketParams inherits it.
    sample_rate: int = getattr(transport._params, "audio_out_sample_rate", 16000)

    # per-call runtime state, shared with the idle handler and the end-call tool
    # so whichever mechanism ends the call can record why.
    call_session = CallSession(call_id=call_id)

    # call metadata row — the transcript rows below are linked to this via db_call_id
    db_call_id = await create_call(
        call_id=call_id,
        transport_type=transport_type,
        stt_provider=settings.stt_provider,
        tts_provider=settings.tts_provider,
        provider=provider,
        caller_name=caller_name,
        phone_number=phone_number,
    )

    # idle handler instance
    idlehandler = IdleHandler(call_session)

    if settings.stt_provider == "sarvam" : 
        logger.info("[pipeline] Sarvam STT chosen") 
        stt = SarvamSTTService(
            api_key=settings.sarvam_api_key.get_secret_value() if settings.sarvam_api_key else "",
            mode="transcribe", 
            sample_rate=sample_rate,
            settings=SarvamSTTSettings(
                model="saaras:v3" , 
            )
        )
        logger.info("[pipeline] Sarvam STT initiated")

    elif settings.stt_provider == "cartesia" : 
        logger.info("[pipeline] Cartiesia stt chosen")
        stt = CartesiaSTTService(
           api_key=settings.cartesia_api_key.get_secret_value() if settings.cartesia_api_key else "",
           sample_rate=sample_rate , 
            settings=CartesiaSTTSettings(
                model="ink-whisper" , 
            )
        )
        logger.info("[pipeline] cartesia stt initiated")
    
    elif settings.stt_provider == "deepgram" : 
        logger.info("[pipeline] Deepgram stt chosen")
        stt = DeepgramSTTService(
            api_key=settings.deepgram_api_key.get_secret_value() if settings.deepgram_api_key else "",
            sample_rate=sample_rate , 
            settings=DeepgramSTTSettings(
                # additional settings to be added here
            )
        )
    
    else: 
        logger.info("[pipeline] elevenlabs stt chosen")
        stt = ElevenLabsRealtimeSTTService(
            api_key=settings.elevenlabs_api_key.get_secret_value() if settings.elevenlabs_api_key else "" ,
            commit_strategy=CommitStrategy.MANUAL ,
            include_timestamps=True , 
            sample_rate=sample_rate , 
            settings=ElevenLabsRealtimeSTTSettings(
                language=Language.EN , 
                model="scribe_v2_realtime",
            )
        ) 
        logging.info("[pipeline] elevenlabs stt initiated")

    if settings.tts_provider == "sarvam": 
        logging.info("[pipeline] Sarvam TTS chosen") 
        tts = SarvamTTSService(
            api_key=settings.sarvam_api_key.get_secret_value() if settings.sarvam_api_key else "",
            pause_frame_processing=True, 
            sample_rate=sample_rate, 
            settings=SarvamTTSSettings(
                model="bulbul:v3" , 
                pace=1.0 , 
                voice="priya", 
            )
        )
        logging.info("[pipeline] sarvam tts initiated")
    
    elif settings.tts_provider == "cartesia": 
        logging.info("[pipeline] cartesia tts chosen")
        tts = CartesiaTTSService(
            api_key=settings.cartesia_api_key.get_secret_value() if settings.cartesia_api_key else "",
            voice_id=settings.cartesia_voice_id,
            pause_frame_processing=True,
            sample_rate=sample_rate ,
            model="sonic-3",
        )
        logging.info("[pipeline] cartesia tts initiated")
    
    elif settings.tts_provider == "deepgram": 
        logging.info("[pipeline] deepgram tts chosen")
        tts = DeepgramTTSService(
            api_key=settings.deepgram_api_key.get_secret_value() if settings.deepgram_api_key else "",
            settings=DeepgramTTSSettings(
                voice="aura-2-helena-en",
            ),
        )
        logging.info("[pipeline] deepgram tts initiated")
    
    else: 
        logging.info("[pipeline] eleven labs tts chosen")
        tts = ElevenLabsTTSService(
            api_key=settings.elevenlabs_api_key.get_secret_value() if settings.elevenlabs_api_key else "", 
            sample_rate=sample_rate , 
            settings=ElevenLabsTTSSettings(
                voice=settings.elevenlabs_voice_id or "", 
                speed=1.0, 
                model="eleven_flash_v2_5", 
            ), 
        )
    
    # silero vad builder 
    silero_vad = SileroVADAnalyzer(
        params=VADParams(
            confidence=0.7,      # Minimum confidence for voice detection
            start_secs=0.2,      # Time to wait before confirming speech start
            stop_secs=0.2,       # Time to wait before confirming speech stop
            min_volume=0.6,      # Minimum volume threshold
        )
    )


    # llm config in pipeline 
    llm = AWSBedrockLLMService(
        aws_access_key=settings.aws_access_key_id or "", 
        aws_secret_key=settings.aws_secret_access_key.get_secret_value() if settings.aws_secret_access_key else "",
        aws_session_token=settings.aws_session_token.get_secret_value() if settings.aws_session_token else None,
        aws_region=settings.aws_region or "ap-south-1",
        settings=AWSBedrockLLMService.Settings(
            model=settings.agent_model_id or "", 
            # enabling prompt caching for models that support it 
            enable_prompt_caching=True,
            # system prompt loaded from prompts module 
            system_instruction=return_prompt() , 
            max_tokens=300,
        )
    )

    # this part holds the short term memmory of the conversation in-memory
    conversation_context = LLMContext(tools=[get_current_datetime , create_end_call_tool(call_session)])

    # the bot and user turn aggregator with customisable parameters 
    user_aggregator , assistant_aggregator = LLMContextAggregatorPair(
        context=conversation_context, 
        user_params=LLMUserAggregatorParams(
            vad_analyzer=silero_vad ,
            # trigger idle handler after 5 seconds of silence
            user_idle_timeout=5.0 ,
            # silence-timeout turn end instead of the default local Smart Turn model,
            # for lower and more predictable turn-taking latency
            user_turn_strategies=UserTurnStrategies(
                stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.6)]
            ),
        ),
        # configuring assistant aggregator  
        # assistant_params=LLMAssistantAggregatorParams(
            # for longer conversation creating conversation summarizers 
            # enable_auto_context_summarization=True ,
            # customising the behaviour of the context summarizer 
            # auto_context_summarization_config=LLMAutoContextSummarizationConfig(
            #     max_context_tokens=10000 , 
            #     max_unsummarized_messages=40 , 
                # configs of the summary stored 
                # summary_config=LLMContextSummaryConfig(
                    # the target summary size 
                    # target_context_tokens=4000 , 
                    # minimum number of messages to be kept in the summary uncompressed 
                    # min_messages_after_summary=10, 
                    # adding a custom summarization prompt if needed 
                    # summarization_prompt="" , 
                    # custom llm (smaller llm ) if needed for summarization 
                    # use an LLM Service 
                    # llm=None , 
            #     )
            # )
        # )
    )

    pipeline = Pipeline([
        transport.input(),
        stt,
        user_aggregator,
        llm,
        tts,
        transport.output(),       
        assistant_aggregator,
    ])


    # user-stopped-speaking -> bot-started-speaking gap — the single number that
    # best reflects what the caller actually perceives as "responsiveness"
    latency_observer = UserBotLatencyObserver()

    worker = PipelineWorker(
        pipeline=pipeline ,
        name="voice accelerator pipeline worker" ,
        observers=[latency_observer],
        params=PipelineParams(
            # enable_metrics=True,
        )
    )

    @latency_observer.event_handler("on_latency_measured")
    async def on_latency_measured(observer, latency):
        logger.info(f"[latency] call={call_id} turn response time: {latency * 1000:.0f}ms")

    # service/provider errors (bad API keys, expired credentials, reconnect
    # failures, etc.) are relayed to the client over the data channel but are
    # NOT printed here by default — without this handler, this terminal stays
    # silent even while the browser shows real errors.
    @worker.event_handler("on_pipeline_error")
    async def on_pipeline_error(worker, frame):
        logger.error(f"[pipeline] call={call_id} error from {frame.processor}: {frame.error}")
    
    # another (simpler and less complex way of handling silences in between the conversation) 

    # @user_aggregator.event_handler("on_user_turn_idle")
    # async def on_user_turn_idle(aggregator): 
    #     """ remind the user """ 
    #     message = {
    #         "role" : "developer" , 
    #         "content" : "The user has been quiet for a while. Politely ask if they can hear you and if they are still there"
    #     }

    #     await aggregator.push_frame(LLMMessagesAppendFrame([message],run_llm=True))

    @user_aggregator.event_handler("on_user_turn_idle")
    async def on_user_turn_idle(aggregator):
        await idlehandler.handle_idle(aggregator=aggregator)

    @user_aggregator.event_handler("on_user_turn_started")
    async def on_user_turn_started(aggregator, strategy):
        await idlehandler.reset()

    # add initial greeting soon as the call connects
    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport , client):
        logger.info("Client connected - starting the conversation")
        greeting_instruction = {
            "role" : "developer" ,
            "content" : "Say Hello to the user , and introduce yourself , make it sound human , Keep it short under 20 words" ,
        }
        await worker.queue_frames([LLMMessagesAppendFrame([greeting_instruction],run_llm=True)])

    # transcript capture — one row per turn (see backend/src/db/models.py for why
    # this isn't a single growing column). Persisted off the frame path via
    # create_task so a slow/unavailable DB never blocks the pipeline.
    @user_aggregator.event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(aggregator, strategy, message: UserTurnStoppedMessage):
        if message.content:
            asyncio.create_task(
                add_message(db_call_id, call_session.next_sequence(), "user", message.content)
            )

    @assistant_aggregator.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(aggregator, message: AssistantTurnStoppedMessage):
        if message.content:
            asyncio.create_task(
                add_message(
                    db_call_id,
                    call_session.next_sequence(),
                    "assistant",
                    message.content,
                    interrupted=message.interrupted,
                )
            )

    # single place the call is marked ended, regardless of what triggered it —
    # the end-call tool, the idle handler's final timeout, or the client
    # disconnecting all terminate the pipeline, which fires this.
    @worker.event_handler("on_pipeline_finished")
    async def on_pipeline_finished(worker, frame):
        asyncio.create_task(end_call(db_call_id, call_session.end_reason))

    return worker