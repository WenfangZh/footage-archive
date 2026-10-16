"""Synthetic media for tests, generated with ffmpeg: never real footage."""

import subprocess

TEST_PICTURE = ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=160x120:rate=10']
TEST_SOUND = ['-f', 'lavfi', '-i', 'sine=frequency=440:duration=1']
H264 = ['-c:v', 'libx264', '-pix_fmt', 'yuv420p']


def run_ffmpeg(*args):
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *args], check=True)
