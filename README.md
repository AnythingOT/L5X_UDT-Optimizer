# L5X UDT Optimizer

A small web app that tidies up Allen-Bradley UDTs. Upload either single UDT export `.l5x` or full program `.l5x`, it will group datatypes correctly and also sort tags by alphabetical order within each datatype.

---

## Output file names

Optimized output files get a prefix, but UDT name or program name itself is not changed.

- A single UDT like `MyUDT.L5X` comes back as `OptimizedUDTfile_MyUDT.l5x`.
- A full program like `PlantProgram.L5X` comes back as `OptimizedProgramfile_UDTsonly_PlantProgram.l5x`. You need to rename this file name before importing else studio5k will throw filename too large fit.

---

## Status messages

When you upload a full program, each UDT gets a quick verdict:

- **Done** — UDT was reordered or repacked.
- **No Change** — UDT was already laid out well, so no change to that UDT.
- **N/A** — every member used a type that isn't defined anywhere in the file you uploaded. This usually means the real definitions only live in the full program export. Upload that instead and they'll resolve. Hover the row to see which types it couldn't find.
- **Error** — something unexpected went wrong; the tooltip has details.

---

## Running it

You'll need Python 3.10 or newer and Flask:

```bash
pip install flask
python app.py
```

It opens at http://127.0.0.1:5001. 

.EXE is in dist folder.

---

## Where this fits

This is one piece of a bigger work-in-progress project, **LLM4ICS & LLM4L5X**.

Still on the list:

- A tag optimizer to go alongside this one, covering UDT, global, and local tags.
- Smarter alignment handling that accounts for differences between firmware versions.
- Tying into the other LLM4ICS modules.
