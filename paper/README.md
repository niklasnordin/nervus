# Validation paper (draft)

A draft in the style of the *Journal of Fluid Mechanics*: Nervus verified against exact solutions, module by
module, and compared with two sets of fluid-mechanical measurements (tank draining through an orifice; Newton's St Paul's Cathedral drop experiments).

Both authors have agreed to the author list. Before submission, the affiliations, keywords and the declaration of
interests still have to be filled in (see the comments at the top of `validation.tex`).

| File | What it is |
|---|---|
| `validation.tex`, `validation.pdf` | the manuscript |
| `analysis.py` | builds both Nervus models, runs every simulation, writes `results.json` and `figures/` |
| `verification.py` | code verification: every module against exact solutions (called by `analysis.py`; run alone to update only that part of `results.json`) |
| `make_numbers.py` | turns `results.json` into `numbers.tex`, the only source of numbers in the text |
| `newton1726_st_pauls.csv` | Newton's table (public domain), with SI conversions |
| `data/` | the tank data of Fabusola & Simon (2025): not redistributed, see `data/README.md` |
| `references.bib` | references, each checked (DOIs against Crossref) |
| `jfm.cls`, `jfm.bst`, `lineno-FLM.sty` | the Cambridge University Press JFM template files: not in the repository (`jfm.cls` may only be used for articles published by CUP). Download the JFM LaTeX author template from the journal's *Preparing your materials* page on cambridge.org and put the three files here to rebuild the PDF |

Rebuild:

```bash
uv run python paper/analysis.py      # about 15 minutes
uv run python paper/make_numbers.py
cd paper && pdflatex validation && bibtex validation && pdflatex validation && pdflatex validation
```
