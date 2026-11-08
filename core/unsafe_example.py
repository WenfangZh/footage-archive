# Throwaway: checks that the security scan fails (SPEC M10 Done when). Never merged.
import subprocess


def run(command):
    return subprocess.run(command, shell=True)
