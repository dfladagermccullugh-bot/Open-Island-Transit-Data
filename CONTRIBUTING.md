# Corrections and contributions

This project publishes numbers about a public service, so a wrong number
matters. If you find one, please tell us.

## Report a wrong number

Open an issue with the "Something looks wrong" form (the site's footer link
"Spotted something off? Tell us" leads there; it needs a free GitHub
account). Say which page or file shows the number, which service day and
route it is about, and what you think it should be and why. If you rode the bus or timed it yourself, say
so: timings taken in person are the best check we have.

Every figure is rebuilt from the saved feed by the code in this repository,
so a mistake is nearly always in the code or in one of the reference files
under `reference/`. When we fix one, we raise the version of the code that
changed, the nightly job rebuilds every affected day, and the change is
recorded in [`docs/decisions.md`](docs/decisions.md).

## Ask for data to be removed

Open an issue with the "Question about the data" form. The feeds
describe vehicles only and hold nothing about riders, but if you believe
something here should not be published, we want to hear it. The terms are
in [`LICENSE-DATA.md`](LICENSE-DATA.md).

## Change the code

```bash
pip install -e ".[dev]" -c constraints.txt
ruff check . && ruff format --check .
mypy
pytest
```

If you change the site, also run `scripts/test_site.sh`, which builds it and
tries every page in a browser.

A change to how a number is computed needs three things: a test that fails
without it, a higher version constant for the stage it touches (for example
`QUALITY_VERSION`), and a line in `docs/decisions.md` saying what changed
and why. `docs/methods.md` and `docs/codebook.md` must say what the code
does. You can run the whole nightly chain on your own machine, publishing
nothing, with `python scripts/rehearse_nightly.py <date> <empty folder>`.

This project is independent and has no connection to Island Transit. Please
send questions about the bus service itself to Island Transit.
