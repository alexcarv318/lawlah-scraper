import argparse

from src.pipeline.pipelines import ActPipeline, CasePipeline, ParagraphPipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Lawlah scrape and knowledge-base pipelines.")
    commands = parser.add_subparsers(dest="command", required=True)

    cases = commands.add_parser("cases", help="Discover, fetch, parse, and promote cases.")
    cases.add_argument("mode", choices=["backfill", "update"])
    cases.add_argument("--max-search-pages", type=int, default=None)
    cases.add_argument("--max-documents", type=int, default=None)
    cases.add_argument("--max-cases", type=int, default=None)

    paragraphs = commands.add_parser(
        "paragraphs",
        help="Classify, extract citations, and embed promoted case paragraphs.",
    )
    paragraphs.add_argument("--max-paragraphs", type=int, default=None)
    paragraphs.add_argument("--max-cases", type=int, default=None)

    acts = commands.add_parser("acts", help="Scrape, parse, promote, classify, and embed acts.")
    acts.add_argument("mode", choices=["backfill"], nargs="?", default="backfill")
    acts.add_argument("--max-acts", type=int, default=None)
    acts.add_argument("--max-versions", type=int, default=None)
    acts.add_argument("--max-provisions", type=int, default=None)

    return parser


def main() -> None:
    arguments = build_parser().parse_args()

    if arguments.command == "cases":
        if arguments.mode == "backfill":
            CasePipeline.backfill(
                max_search_pages=arguments.max_search_pages,
                max_documents=arguments.max_documents,
                max_cases=arguments.max_cases,
            )
        else:
            CasePipeline.update(
                max_search_pages=arguments.max_search_pages,
                max_documents=arguments.max_documents,
                max_cases=arguments.max_cases,
            )
        return

    if arguments.command == "paragraphs":
        ParagraphPipeline.run(
            max_paragraphs=arguments.max_paragraphs,
            max_cases=arguments.max_cases,
        )
        return

    ActPipeline.backfill(
        max_acts=arguments.max_acts,
        max_versions=arguments.max_versions,
        max_provisions=arguments.max_provisions,
    )


if __name__ == "__main__":
    main()
