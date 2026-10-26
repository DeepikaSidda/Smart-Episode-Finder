"""Before/after demo.

Runs the SAME question through the SAME agent twice: once with the Amazon Transcribe
tool withheld (the agent's original capability) and once with it available.

Usage:
    python -m agent.demo
    python -m agent.demo "your own question"
"""
import logging
import os
import sys

# Resolve the YouTube key from Parameter Store rather than hardcoding it.
os.environ.setdefault("YOUTUBE_API_KEY_PARAM", "/smart-podcast-finder/youtube_api_key")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.podcast_agent import ask  # noqa: E402

DEFAULT_QUESTION = (
    "On the Syntax podcast, in the episode called 'Deno Is Dead', which specific AI "
    "models did the hosts actually talk about? Quote them."
)


def ascii_safe(s):
    return str(s).encode("ascii", "replace").decode("ascii")


def show(label, result):
    print()
    print("=" * 78)
    print(label)
    print("=" * 78)
    print(f"  tools available : {', '.join(result['tools_available'])}")

    tools = result["tools_used"]
    if not tools:
        print("  tools called    : (none)")
    else:
        counts = {}
        for t in tools:
            counts[t["tool"]] = counts.get(t["tool"], 0) + 1
        summary = ", ".join(
            f"{name}{f' x{n}' if n > 1 else ''}" for name, n in counts.items()
        )
        print(f"  tools called    : {summary}")
        args = {k: v for k, v in tools[0].items() if k != "tool"}
        print(f"  first call args : {args}")
    print()
    print(ascii_safe(result["answer"]))


def main():
    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION

    print()
    print("QUESTION:", ascii_safe(question))

    before = ask(question, with_ears=False)
    show("BEFORE  -  metadata only (no Amazon Transcribe tool)", before)

    after = ask(question, with_ears=True)
    show("AFTER   -  with the Amazon Transcribe 'listen' tool", after)

    print()
    print("=" * 78)
    print("CONTRAST")
    print("=" * 78)
    before_tools = [t["tool"] for t in before["tools_used"]]
    after_tools = [t["tool"] for t in after["tools_used"]]
    print(f"  before tools : {before_tools or 'none'}")
    print(f"  after tools  : {after_tools or 'none'}")
    print(f"  listened     : {'listen_to_episode' in after_tools}")
    print(f"  model        : {after['model']}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    main()
