"""Measure the chat template overheads of the Ministral-3-3B profile, and check token counts.

This is a dev tool. It uses `mistral-common` (Apache-2.0), Mistral's reference tokenizer, and the
locked `tekken.json`. It needs no generator and no model weights (ADR 0007).

    uv run python tools/measure_profile.py            # print the measured overheads
    uv run python tools/measure_profile.py --check    # check our counts against mistral-common (CI)
"""

import argparse
import sys
from pathlib import Path

from mistral_common.protocol.instruct.messages import SystemMessage, UserMessage
from mistral_common.protocol.instruct.request import ChatCompletionRequest
from mistral_common.tokens.tokenizers.mistral import MistralTokenizer
from xat_rag_tokenizers import MINISTRAL_3_3B, HfTokenCounter

ROOT = Path(__file__).resolve().parent.parent
FOLDER = ROOT / "models" / "ministral-3-3b-tokenizer"

CORPUS = [
    "Hello world.",
    "2026-11-07T10:00:03.001Z ERROR [orders] com.shop.OrderNotFoundException: order 9f1c not found (PROJ-123)",
    "    at com.shop.OrderService.find(OrderService.java:88)",
    '<chunk id="C1" kind="jira" trust="untrusted">\nPROJ-123: Order lookup fails\nNPE in @user\n</chunk>',
    "def parse(self, raw: RawDocument) -> ParsedDocument:\n    return ParsedDocument(...)",
    "Überprüfen Sie die Konfiguration. Почему ошибка? 为什么失败 🙂",
    "| port | 443 |\n| host | 127.0.0.1 |",
    "   leading and trailing spaces   ",
]


def _chat_tokens(tokenizer: MistralTokenizer, system: str, user: str) -> int:
    request = ChatCompletionRequest(messages=[SystemMessage(content=system), UserMessage(content=user)])
    return len(tokenizer.encode_chat_completion(request).tokens)


def main() -> None:
    """Measure, or check."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    reference = MistralTokenizer.from_file(str(FOLDER / "tekken.json"))
    raw = reference.instruct_tokenizer.tokenizer
    ours = HfTokenCounter(FOLDER / "tokenizer.json", MINISTRAL_3_3B.tokenizer_id)

    mismatches = [
        (text, ours.count(text), len(raw.encode(text, bos=False, eos=False)))
        for text in CORPUS
        if ours.count(text) != len(raw.encode(text, bos=False, eos=False))
    ]
    for text, mine, theirs in mismatches:
        print(f"MISMATCH ours={mine} mistral-common={theirs}: {text[:60]!r}")

    samples = [("You triage logs.", "Why does it fail?"), ("A" * 50, "B c d " * 40), (CORPUS[3], CORPUS[1])]
    overheads = []
    for system, user in samples:
        total = _chat_tokens(reference, system, user)
        overheads.append(total - ours.count(system) - ours.count(user))
    print(f"measured overhead for one system and one user message: {overheads}")
    profile = MINISTRAL_3_3B
    print(f"profile: template_overhead={profile.template_overhead} per_message={profile.per_message_overhead}")
    budgeted = MINISTRAL_3_3B.template_overhead + 2 * MINISTRAL_3_3B.per_message_overhead
    if args.check and (mismatches or max(overheads) > budgeted):
        sys.exit("The tokenizer or the profile does not match mistral-common.")


if __name__ == "__main__":
    main()
