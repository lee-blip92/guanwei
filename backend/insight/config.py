import os
import sys
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / '.env')
DATA = Path(os.getenv('INSIGHT_DATA_DIR', str(ROOT / 'runtime'))).resolve()
CRAWLER = Path(os.getenv('MEDIACRAWLER_PATH', str(ROOT / 'md'))).resolve()
default_python = CRAWLER / ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python')
CRAWLER_PYTHON = os.getenv('MEDIACRAWLER_PYTHON') or str(default_python if default_python.exists() else sys.executable)

def model_defaults():
    return {
        'base_url': os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1'),
        'model': os.getenv('OPENAI_MODEL', 'gpt-4o-mini'),
        'batch_size': max(1, min(20, int(os.getenv('MODEL_BATCH_SIZE', '10')))),
        'timeout': max(5, min(180, int(os.getenv('MODEL_TIMEOUT', '90')))),
    }
