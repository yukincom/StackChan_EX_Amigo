from pathlib import Path
import shutil
import subprocess

import pytest


def _build_and_run(root: Path, sources: Path, tmp_path: Path):
    """Use already-installed decoder sources; never install dependencies here."""
    audio = root / "firmware/.pio/libdeps/m5stack-cores3/ESP8266Audio/src"
    if not (audio / "AudioGeneratorMP3.cpp").is_file():
        pytest.skip("optional MP3 integration requires existing CoreS3 ESP8266Audio dependency")
    executables = {name: shutil.which(name) for name in ("cc", "c++", "ffmpeg")}
    missing = [name for name, executable in executables.items() if not executable]
    if missing:
        pytest.skip("optional MP3 integration requires " + ", ".join(missing))

    def run(arguments):
        return subprocess.run(arguments, check=True, capture_output=True, text=True, timeout=90)

    wav = tmp_path / "synthetic.wav"
    standard = tmp_path / "standard-id3.mp3"
    raw = tmp_path / "raw.mp3"
    run([
        executables["ffmpeg"], "-nostdin", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-ar", "24000", "-ac", "1", str(wav),
    ])
    # Match openai_compat_routes._wav_to_mp3, including its default ID3 metadata.
    conversion = [
        executables["ffmpeg"], "-y", "-loglevel", "error", "-i", str(wav),
        "-vn", "-acodec", "libmp3lame", "-b:a", "128k",
    ]
    run([*conversion, str(standard)])
    run([*conversion, "-id3v2_version", "0", "-write_xing", "0", str(raw)])

    stubs = sources / "mp3_stubs"
    includes = ["-I", str(stubs), "-I", str(audio), "-I", str(root / "firmware/src")]
    objects = []
    for source in sorted((audio / "libmad").glob("*.c")):
        obj = tmp_path / (source.stem + ".o")
        run([executables["cc"], "-std=c99", "-O0", *includes, "-c", str(source), "-o", str(obj)])
        objects.append(str(obj))
    binary = tmp_path / "mp3-playback"
    run([
        executables["c++"], "-std=c++17", "-O0", *includes,
        str(sources / "mp3_playback.cpp"),
        str(audio / "AudioGeneratorMP3.cpp"), str(audio / "AudioLogger.cpp"),
        str(audio / "AudioFileSourceBuffer.cpp"), *objects, "-o", str(binary),
    ])
    result = run([str(binary), str(standard), str(raw)])
    assert "PASS normal-id3" in result.stdout
    assert "PASS valid-empty-valid-reuse" in result.stdout
    return result.stdout


def test_actual_mp3_decoder_and_production_playback_completion(tmp_path):
    root = Path(__file__).resolve().parents[2]
    _build_and_run(root, root / "tests/firmware", tmp_path)
