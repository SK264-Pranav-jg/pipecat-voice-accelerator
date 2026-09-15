"""
notes : 
 resamples audio mp3 files to a specific sample rate 


Upload the necessary input path , and output path of the mp3 file and get the resampled mp3 file with 
the desired sample rate
""" 

import asyncio
import wave
from pydub import AudioSegment
from pipecat.audio.utils import create_file_resampler
from static_ffmpeg import add_paths
add_paths() 


async def resample_audio_file(input_path: str, output_path: str, out_rate: int):
    # decode mp3 -> raw PCM via pydub/ffmpeg
    audio = AudioSegment.from_file(input_path, format="mp3")
    audio = audio.set_sample_width(2)  # force 16-bit PCM (soxr requirement)

    in_rate = audio.frame_rate
    n_channels = audio.channels
    sample_width = audio.sample_width
    pcm_bytes = audio.raw_data

    if sample_width != 2:
        raise ValueError("SOXRAudioResampler expects 16-bit PCM input")

    resampler = create_file_resampler()
    resampled_bytes = await resampler.resample(pcm_bytes, in_rate=in_rate, out_rate=out_rate)

    # write resampled PCM to a wav file (soxr only guarantees PCM out, so output as .wav)
    with wave.open(output_path, "wb") as out_wf:
        out_wf.setnchannels(n_channels)
        out_wf.setsampwidth(sample_width)
        out_wf.setframerate(out_rate)
        out_wf.writeframes(resampled_bytes)


if __name__ == "__main__":
    asyncio.run(resample_audio_file(
        # just change the path of the input and the output file as per the requirement 
        input_path="",
        output_path="",
        # can change the out_rate according to the requirement 
        # use mp3 files 
        out_rate=16000,
    ))