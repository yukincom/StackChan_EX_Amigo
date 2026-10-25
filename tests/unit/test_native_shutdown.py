"""Exercise native teardown, not only successful imports or an output marker."""

from pathlib import Path
import subprocess
import sys
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("order", ["sentencepiece_first", "curated_first"])
def test_sentencepiece_and_curated_tokenizers_exit_normally(tmp_path, order):
    # spaCy loads curated-tokenizers via its installed pipeline registry. This
    # exact pair reproduced the crash without spaCy, Kokoro, MLX, or any model.
    imports = (
        "import sentencepiece as sp\nimport curated_tokenizers\n"
        if order == "sentencepiece_first"
        else "import curated_tokenizers\nimport sentencepiece as sp\n"
    )
    code = imports + """
import io
text = 'Hello world 日本語の確認です。'
model = io.BytesIO()
sp.SentencePieceTrainer.train(
    sentence_iterator=iter([text] * 10), model_writer=model,
    vocab_size=64, hard_vocab_limit=False, minloglevel=2,
)
tokenizer = sp.SentencePieceProcessor(model_proto=model.getvalue())
ids = tokenizer.encode(text, out_type=int)
assert tokenizer.decode(ids) == text
curated = curated_tokenizers.SentencePieceProcessor.from_protobuf(model.getvalue())
assert curated.encode_as_ids(text) == ids
assert curated.decode_from_ids(ids) == text
print('NATIVE_SHUTDOWN_BODY_OK', flush=True)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    # The old wheel printed BODY_OK and then died with SIGBUS: both are needed.
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert "NATIVE_SHUTDOWN_BODY_OK" in result.stdout


def test_mlx_and_test_dependencies_keep_fixed_sentencepiece():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert any(
        item.startswith("sentencepiece>=0.2.2")
        for item in project["project"]["optional-dependencies"]["mlx"]
    )
    assert "sentencepiece>=0.2.2" in project["dependency-groups"]["test"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    package = next(p for p in lock["package"] if p["name"] == "sentencepiece")
    assert tuple(int(n) for n in package["version"].split(".")) >= (0, 2, 2)
