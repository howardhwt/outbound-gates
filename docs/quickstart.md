# Quickstart

Two parts. First, watch the gate block a draft and then let it through, on a tiny bundled demo: no setup, no CRM, no network. Then set it up on your own outbound folder.

You need Python 3.9 or later. Nothing else installs.

## 1. Try it on the demo (two minutes)

In Claude Code with the plugin installed, type `/outbound-gates:try`. It runs the steps below for you.

To run them yourself, point `OG` at the plugin folder: your clone of this repo, or the installed copy (`~/.claude/plugins/cache/outbound-gates/outbound-gates/<version>`).

```sh
OG=/path/to/outbound-gates
```

Copy the demo project. It has one fictional company, its pricing page (served from a local file) and one draft that quotes one price.

```sh test
PYTHONPATH="$OG" python3 -m gates.quickstart try-demo
```

Run the gate on the draft. It is blocked: the draft says `$0.006`, and nothing records where that number came from. The refusal prints the command that fixes it. Exit code 1 means blocked.

```sh test exit=1
PYTHONPATH="$OG" python3 -m gates.evaluate try-demo/draft.txt --project try-demo --to dana@quickstart-co.example
```

Record the source: the exact sentence on the company's own pricing page. The gate reads the page, checks the sentence is there and contains the number, and keeps a copy of the page with its hash.

```sh test
PYTHONPATH="$OG" python3 -m gates.evidence record --gates-home "$PWD/try-demo/.gates" --contact dana@quickstart-co.example --url https://quickstart-co.example/pricing --quote 'Transcription is billed at $0.006 per audio minute' --value '$0.006'
```

Run it again. It passes.

```sh test
PYTHONPATH="$OG" python3 -m gates.evaluate try-demo/draft.txt --project try-demo --to dana@quickstart-co.example
```

Try changing the draft: write `$0.005`, or add "about 40% less". Each new number is blocked until it has a source or a recorded calculation (see "Recording a calculation" in `docs/send-gate.md`).

## 2. Use it on your own project

1. **Install the plugin for your outbound folder only.** In that folder: `/plugin marketplace add howardhwt/outbound-gates`, then `/plugin install outbound-gates@outbound-gates` and pick the local or project scope. At user scope it would block Gmail sends in every project.
2. **List your own facts** (your prices and product numbers, which pass without a source) in `~/.config/outbound-gates/config.json`, for example `{"own_facts": ["$0.01"]}`. Names with digits in them, such as a model `H3` or `1080p`, go in `"names"`. Your CRM's logging BCC address, if it has one, goes in `"crm_bcc"`.
3. **Run setup.** Type `/outbound-gates:setup` in your outbound folder. It previews the plan first, and you apply it yourself. It installs the safety net that holds when the plugin isn't loaded, and writes your project's writing style to `.gates/style.json`: neutral by default, `--style example` for the example house style, or `--style FILE` for your own. See "Truth rules and style" in the README.
4. **Connect your CRM.** A local export (`--crm-source file --crm-path PATH`) or live HubSpot (`--crm-source hubspot --sender-owner ID`, with a read-only key in `~/.config/outbound-gates/hubspot.key`). See `docs/send-gate.md`.
5. **Start a new Claude Code session there.** The first line says `send gate: on`. From then on, every Gmail draft the agent creates is checked, and send tools are blocked: a person sends.

To check a draft from the command line at any time:

```sh
PYTHONPATH="$OG" python3 -m gates.evaluate draft.txt --project . --to name@company.com --subject "Your subject"
```

Stuck? Open an issue with the "Stuck setting up" template.
