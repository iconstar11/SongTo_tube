import subprocess
import sys
import shutil
from pathlib import Path
from config import DEMUCS_MODEL, TEMP_DIR

def run(audio_path: Path) -> Path:
    """Run Demucs to extract vocals."""
    vocals_path = TEMP_DIR / f"{audio_path.stem}_vocals.wav"
    
    # Check if already exists (resume)
    if vocals_path.exists():
        return vocals_path

    subprocess.run([
        sys.executable, "-m", "demucs",
        "--two-stems=vocals", "-n", DEMUCS_MODEL,
        str(audio_path), "-o", str(TEMP_DIR),
    ], check=True)

    # Demucs outputs to: {TEMP_DIR}/{model}/{stem}/vocals.wav
    demucs_out = TEMP_DIR / DEMUCS_MODEL / audio_path.stem / "vocals.wav"
    if demucs_out.exists():
        shutil.move(str(demucs_out), str(vocals_path))
        shutil.rmtree(TEMP_DIR / DEMUCS_MODEL, ignore_errors=True)
        return vocals_path
    else:
        raise Exception(f"Demucs output not found at {demucs_out}")
