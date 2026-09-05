"""Compatibility entry point for the role-separated, offline text lab."""
from .conversation_lab.context import (
    DEFAULT_CONTEXT_CHARS, DEFAULT_HISTORY_UTTERANCES, TextContext, Utterance, validate_names,
)
from .conversation_lab.reader import LocalSceneReader, NoRedirect, PROMPT, SCENE_KEYS, parse_scene
from .conversation_lab.cases import load_cases
from .conversation_lab.evaluation import replay_case, reply_demos, row_passes, score_rows
from .conversation_lab.report import check_report_path, save_report
from .conversation_lab.cli import interactive, main


if __name__ == "__main__":
    raise SystemExit(main())
