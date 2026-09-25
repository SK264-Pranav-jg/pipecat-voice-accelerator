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
from pipecat.frames.frames import LLMMessagesAppendFrame , TTSSpeakFrame , LLMRunFrame , EndWorkerFrame

# greeting interruption strategy 
from pipecat.turns.user_mute import MuteUntilFirstBotCompleteUserMuteStrategy 

# rnn filter 
from pipecat.audio.filters.rnnoise_filter import RNNoiseFilter

# text aggregation mode 
from pipecat.services.tts_service import TextAggregationMode

# pipecat-vobiz import 
from pipecat.serializers.vobiz import VobizFrameSerializer , parse_vobiz_start 

# strands agents plugin , (plugin to be used in case of using strands agent instead pipecat native LLMServices) 
# use this when migrating from LLMServices to Strands Agents (do not implement Custom Classes) 
# please refrain from using strands agents unless or until non negotiable since it loses a lot of event handlers and ease of adding features 
# to the bot and could add additional latency 
from pipecat.processors.frameworks.strands_agents import StrandsAgentsProcessor 

# background ambience imports 
# import to add background ambient sounds to the bot 
from pipecat.audio.mixers.soundfile_mixer import SoundfileMixer 

# aws llm 
from pipecat.services.aws.llm import AWSBedrockLLMService , AWSBedrockLLMSettings 

# google llm service import (optional) 
from pipecat.services.google.llm import GoogleLLMService , GoogleLLMSettings 

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
# Silero vad only supports 16khz and 8khz so adjust accordingly 
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams

# turn-taking — SpeechTimeoutUserTurnStopStrategy (silence timeout) instead of the
# framework default LocalSmartTurnAnalyzerV3 (a local ML model run on every turn) to
# keep response latency low and predictable
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy

# latency visibility — enable_metrics=True alone collects nothing you can see
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.observers.startup_timing_observer import StartupTimingObserver

# frame processor 
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.frames.frames import AudioRawFrame

# backend import
from backend.src.pipecat.idle_handler import IdleHandler
from backend.src.pipecat.call_session import CallSession
from backend.src.config.settings import settings
from backend.src.ai.prompts.prompt_main import return_prompt

# tool imports
from backend.src.ai.tools.current import get_current_datetime
from backend.src.ai.tools.end_call import create_end_call_tool
from backend.src.ai.tools.retrieval import query_knowledge_base 

# db imports (call metadata + transcript persistence)
from backend.src.db.repository import create_call, add_message, end_call

# vobiz active-call tracking cleanup
from backend.src.api.vobiz_telephony import pop_active_vobiz_call

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
        # default is 10.0s — if the client disconnects while the bot is mid-response, the
        # output transport still has to try writing that already-queued audio somewhere and
        # blocks for the full timeout before giving up on the dead connection. Our
        # on_client_disconnected handler fires immediately, but EndWorkerFrame queues behind
        # whatever TTS/output frames were already in flight, so this timeout is what actually
        # gates teardown in that case. Shortened so a truly dead peer is detected in a few
        # seconds instead of ~10-20s; if you see real (but slow) connections getting dropped
        # under normal network hiccups, raise this back up.
        audio_out_write_timeout_secs=3.0,
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
        # see the webrtc entry above — same reasoning applies if the callee hangs up while
        # the bot is mid-response.
        audio_out_write_timeout_secs=3.0,
        # background/line noise filter — RNNoise internally resamples 8kHz <-> 48kHz,
        # so it works fine at telephony rates. Suppressing line hiss/comfort noise here
        # helps VAD get a clean speaking/not-speaking signal instead of flickering on noise.
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

    # frame processor to log audio frames 
    class AudioDebugProcessor(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)  
            if isinstance(frame, AudioRawFrame):
                logger.info(
                    f"[audio-debug] got audio: {len(frame.audio)} bytes, "
                    f"rate={frame.sample_rate}, channels={frame.num_channels}"
                )
            await self.push_frame(frame, direction)

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
                model="ink-2" , 
            ), 
            ttfs_p99_latency=0.35
        )
        logger.info("[pipeline] cartesia stt initiated")
    
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
            sample_rate=sample_rate ,
            text_aggregation_mode=TextAggregationMode.TOKEN , 
            settings=CartesiaTTSSettings(
                model="sonic-3",
                voice=settings.cartesia_voice_id,
            ),
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
    vad_params = VADParams(
        confidence=0.7,
        start_secs=0.2,
        stop_secs=0.2,
        min_volume=0.6,
    )
    silero_vad = SileroVADAnalyzer(sample_rate=8000,params=vad_params)


    # llm config in pipeline 
    llm = AWSBedrockLLMService(
        aws_access_key=settings.aws_access_key_id or "", 
        aws_secret_key=settings.aws_secret_access_key.get_secret_value() if settings.aws_secret_access_key else "",
        aws_session_token=settings.aws_session_token.get_secret_value() if settings.aws_session_token else None,
        aws_region=settings.aws_region or "ap-south-1",
        settings=AWSBedrockLLMService.Settings(
            model=settings.agent_model_id or "", 
            # enabling prompt caching for models that support it (claude models) 
            # enable_prompt_caching=True,
            # system prompt loaded from prompts module 
            system_instruction=return_prompt() , 
            max_tokens=100,
        )
    )

    # gemini 
    # llm = GoogleLLMService(
    #     api_key=settings.gemini_api_key.get_secret_value() , 
    #     settings=GoogleLLMSettings(
    #         model="gemini-3.6-flash" , 
    #         system_instruction=return_prompt() ,
    #         max_tokens=100, 

    #     )
    # )

    # this part holds the short term memmory of the conversation in-memory
    conversation_context = LLMContext(tools=[get_current_datetime , create_end_call_tool(call_session), query_knowledge_base])

    # the bot and user turn aggregator with customisable parameters 
    user_aggregator , assistant_aggregator = LLMContextAggregatorPair(
        context=conversation_context, 
        user_params=LLMUserAggregatorParams(
            vad_analyzer=silero_vad ,
            # trigger idle handler after 5 seconds of silence
            user_idle_timeout=5.0 ,
            # use this when you don't want the user to interrupt the greeting message
            user_mute_strategies=[
                MuteUntilFirstBotCompleteUserMuteStrategy(),
            ],
            # for lower and more predictable turn-taking
            user_turn_strategies=UserTurnStrategies(
                stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.4)]
            ),
            # if the stt turns are not transcribed properly 
            user_turn_stop_timeout=5.0 ,
        ),
        # configuring assistant aggregator  
        # assistant_params=LLMAssistantAggregatorParams(
            # # for longer conversation creating conversation summarizers 
            # enable_auto_context_summarization=True ,
            # # customising the behaviour of the context summarizer 
            # auto_context_summarization_config=LLMAutoContextSummarizationConfig(
            #     max_context_tokens=10000 , 
            #     max_unsummarized_messages=40 , 
               #  # configs of the summary stored 
                # summary_config=LLMContextSummaryConfig(
                    ##  the target summary size 
                    # target_context_tokens=4000 , 
                    # # minimum number of messages to be kept in the summary uncompressed 
                    # min_messages_after_summary=10, 
                    # # adding a custom summarization prompt if needed 
                    # summarization_prompt="" , 
                    # # ustom llm (smaller llm ) if needed for summarization 
                    # # use an LLM Service 
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

    # startup timing observer 
    startup_observer = StartupTimingObserver() 

    @startup_observer.event_handler("on_startup_timing_report")
    async def on_startup_timing_report(observer, report):
        logger.info(f"Total startup: {report.total_duration_secs:.3f}s")
        for timing in report.processor_timings:
            logger.info(f"  {timing.processor_name}: setup={timing.setup_duration_secs:.3f}s")


    @latency_observer.event_handler("on_latency_measured")
    async def on_latency_measured(observer, latency):
        logger.info(f"[latency] call={call_id} turn response time: {latency * 1000:.0f}ms")
    
    @latency_observer.event_handler("on_latency_breakdown")
    async def on_latency_breakdown(observer,breakdown): 
        for line in breakdown.turn_contribution_lines():
            logger.info(line) 


    worker = PipelineWorker(
        pipeline=pipeline ,
        name="voice accelerator pipeline worker" ,
        observers=[latency_observer,startup_observer],
        params=PipelineParams(
            enable_usage_metrics=True, 
            enable_metrics=True,
            audio_in_sample_rate=8000, 
            audio_out_sample_rate=8000,
        )
    )

    # service/provider errors (bad API keys, expired credentials, reconnect
    # failures, etc.) are relayed to the client over the data channel but are
    # not printed here by default — without this handler, this terminal stays
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

    # fires when a turn opened (VAD/transcription said the caller started talking) but no
    # stop strategy ever resolved it within user_turn_stop_timeout — most commonly, STT never
    # produced even an interim transcript in time. Left unhandled, the framework silently
    # drops the turn with zero content and never calls the LLM: the caller spoke, got no
    # response, and has no idea why. This turns that dead silence into an actual spoken
    # recovery instead.
    @user_aggregator.event_handler("on_user_turn_stop_timeout")
    async def on_user_turn_stop_timeout(aggregator):
        message = {
            "role": "developer",
            "content": "You didn't catch what the caller just said — their speech never came through. Briefly and politely ask them to repeat themselves.",
        }
        await aggregator.push_frame(LLMMessagesAppendFrame([message], run_llm=True))

    # add initial greeting soon as the call connects
    # @transport.event_handler("on_client_connected")
    # async def on_client_connected(transport , client):
    #     logger.info("Client connected - starting the conversation")
    #     # greeting_instruction = {
    #     #     "role" : "developer" ,
    #     #     "content" : "Say Hello to the user , and introduce yourself , make it sound human , Keep it short under 10 words" ,
    #     # }
    #     # await worker.queue_frames([LLMMessagesAppendFrame([greeting_instruction],run_llm=True)])
    #     await worker.queue_frames([TTSSpeakFrame(f"Hello this is Jane how may I help you ?")])

    # initial greeting as soon as the call connects 
    @worker.event_handler("on_pipeline_started") 
    async def on_pipeline_started(worker,frame): 
        logger.info("Pipeline started - initial greeting playing")
        # greeting_instruction = {
        #     "role" : "developer" ,
        #     "content" : "Say Hello to the user , and introduce yourself , make it sound human , Keep it short under 10 words" ,
        # }
        # await worker.queue_frames([LLMMessagesAppendFrame([greeting_instruction],run_llm=True)])
        await worker.queue_frames([TTSSpeakFrame(f"Hello this is Jane how may I help you ?")])


    # closing connections 
    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info(f"[pipeline] call={call_id} client disconnected — ending pipeline immediately")
        call_session.end_reason = "client_disconnected"
        await worker.queue_frames([EndWorkerFrame()])

    # transcript capture — one row per turn (see backend/src/db/models.py for why
    # this isn't a single growing column). Persisted off the frame path via
    # create_task so a slow/unavailable DB never blocks the pipeline.
    @user_aggregator.event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(aggregator, strategy, message: UserTurnStoppedMessage):
        if message.content:
            asyncio.create_task(
                add_message(db_call_id, call_session.next_sequence(), "user", message.content)
            )
            logger.info("-------------------------")
            logger.info(f"User said : {message.content}")
            logger.info("-------------------------")

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
            logger.info("-------------------------")
            logger.info(f"Assistant said : {message.content}")
            logger.info("-------------------------")
        
    @llm.event_handler("on_function_calls_started")
    async def on_function_calls_started(service,function_calls): 
        func_name = function_calls[0].function_name 
        if func_name != 'query_knowledge_base':
            return 
        
        ack_context = LLMContext(messages=[
            {
                "role" : "user" ,
                "content" : f"Function being called {func_name}"
            }
        ])

        # run_inference() falls back to the service's own system_instruction (the
        # full Pulse persona prompt) when none is passed here — and Bedrock's
        # adapter discards any system message already inside ack_context in favor
        # of that fallback. Passing it explicitly is the only way to actually use
        # this short acknowledgement instruction instead.
        acknowledgement = await service.run_inference(
            ack_context,
            system_instruction="""
            Generate a brief, natural acknowledgement to the caller's last statement.
            Sound like a warm, attentive human support representative.

            Like you are searching for the info they are looking for 
            Do not mention tools, searches, databases, knowledge bases, fetching,
            checking, processing, or waiting. Do not answer the question or ask one.

            Use 3-8 words, vary the phrasing naturally, and return only the acknowledgement.
            """, 
        )
        if acknowledgement:
            await tts.queue_frame(TTSSpeakFrame(acknowledgement,append_to_context=False))

    # single place the call is marked ended, regardless of what triggered it —
    # the end-call tool, the idle handler's final timeout, or the client
    # disconnecting all terminate the pipeline, which fires this.
    @worker.event_handler("on_pipeline_finished")
    async def on_pipeline_finished(worker, frame):
        asyncio.create_task(end_call(db_call_id, call_session.end_reason))
        # clears the in-memory outbound-call metadata stash regardless of what ended the
        # call (bot's end_call tool, idle timeout, callee hangup) — not just the explicit
        # /vobiz/calls/{call_id}/hangup endpoint. No-op for webrtc calls / unknown call_id.
        pop_active_vobiz_call(call_id)
        logger.info("call ended and db record saved")

    return worker