# Project Memory — Index

> Titles only. One line per task, newest at the bottom.
> Full detail for each entry lives in [MASTER_MEMORY.md](./MASTER_MEMORY.md) under the matching `## [ID]` heading.

| ID | Date | Branch | Title |
|----|------|--------|-------|
| 0001 | 2026-05-29 | Nggaev | Set up project memory system (INDEX + MASTER_MEMORY) |
| 0002 | 2026-05-29 | Nggaev | Document memory system in CLAUDE.md for other sessions |
| 0003 | 2026-05-29 | Nggaev-v2 | Cut Nggaev-v2 off DaddysBranch (CLI router) + ported flow-v2 docs/registry |
| 0004 | 2026-05-29 | Nggaev-v2 | Phase 1: Flow v2 schemas + provider fallback + task→model policy (TDD, 67 green) |
| 0005 | 2026-05-29 | Nggaev-v2 | Adopt rewritten (codebase-grounded) plan; prune Phase 1 to PR-scope (55 green) |
| 0006 | 2026-05-29 | Nggaev-v2 | PR-1: source map — extract_source_map + persist source_map_json (TDD, 58 green) |
| 0007 | 2026-05-29 | Nggaev-v2 | PR-4: Boss Arena (Why→How→What) content phase, replaces final-challenge in flows (TDD, 64 green) |
| 0008 | 2026-05-29 | flow-v2-integration | Integrate teammate PRs: Learning(PR#3) DONE+verified (75 green); Practice/alphaq(PR#2) NOT usable as-is; reflection KEPT per New_Flow.md; consolidation→remove; Practice Arc pending |
| 0009 | 2026-05-29 | flow-v2-integration | PR-3 Practice Arc: 6 typed games (3 schemas — RLC + ErrorDetection + shared CbpModeGame), curated per-subject arcs, removed game-breaks/real-life/consolidation, kept reflection; 168 green + claude smoke OK; plan §6/§9 patched |
| 0010 | 2026-05-29 | Nggaev-v2 | PR #1 (molotov) assessed: parallel Phase 2, conflicts — NOT merged. Harvested plan-endorsed only: SourceMap threading (§10 fidelity, smoke OK) + empty-retry + subset-PDF extract; added opencode provider (W1 done). Skipped registry/QA/division-mapper. 190 green |
| 0011 | 2026-05-29 | Nggaev-v2 | PR-5 assembly reshape (plan §8: title/book/summary/Source Map/divisions/reflection; pure renderer; double-heading fix); migration chain 0012-0015 verified offline (single head, clean DDL — live apply needs Docker). All 5 Flow v2 PRs done. 197 green |
| 0012 | 2026-05-30 | Nggaev-v2 | Live real-textbook smoke + fixes: gemini_api_key optional, .gitignore inline-comment bug (almost committed copyrighted PDF), opencode stdin (positional broke on Win 32k limit), gemini -p "." (newer cli rejects empty). FINDINGS: claude refuses copyrighted extraction; opencode/deepseek too weak (0 TOC); gemini is the extractor; TOC reads only first ~10 pages (misses back-of-book contents). DB live on :5433 |
