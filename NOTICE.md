# NOTICE: third-party origin of code in this repository

SupermAP's own code is licensed under the GNU Affero General Public License, version 3 (`LICENSE`). This file covers code whose origin is third-party. The AGPL does not relicense it: if `server/satwatch/sam31.py` is a derivative work of the SAM Materials, it remains under the SAM License (`licenses/SAM-LICENSE.txt`).

## SAM mask codec (`server/satwatch/sam31.py`)

The functions that decode the SAM 3.1 mask payload (`decode_mask`, the
85-symbol text packing, the one-bit range coder, and `raster_to_rings`
which traces an outline from the decoded raster) implement the wire
encoding that the Meta Model API uses for `sam-3.1` segmentation replies.

Origin of the algorithm: the behaviour of Meta's `meta-sam-parser`
(Meta Platforms, Inc.; <https://pypi.org/project/meta-sam-parser/>,
version 0.1.0), which is distributed under the
**SAM License** (last updated November 19, 2025). A verbatim copy of that
licence, taken from the `LICENSE` file of the 0.1.0 source distribution
(sha256 of the file `4dea99bf...ae1be`), is kept in
[`licenses/SAM-LICENSE.txt`](licenses/SAM-LICENSE.txt).

What this repository does and does not contain:

- No source file of `meta-sam-parser` is bundled here. The decoder in
  `sam31.py` is a separate implementation. It decodes 15 real recorded
  replies bit for bit.
- This tree does not record whether the package source was consulted when the
  decoder was written.
- The SAM License says that distribution of the SAM Materials "and any
  derivative works thereof" is under that licence and needs a copy of it
  (section 1.b.i), and that use "will not involve or encourage others to
  reverse engineer, decompile or discover the underlying components of the
  SAM Materials" (section 1.b.iv). Whether the decoder counts as a derivative
  work, and how section 1.b.iv applies, is a legal question this file does not
  answer.
- Counts and bounding boxes never depend on the codec. Outlines are optional:
  set `SATWATCH_SAM_OUTLINES=0` (or build `MetaSamDetector(outlines=False)`)
  and no mask is decoded at all; every object keeps its box and its count.

## If you redistribute or deploy this repository

1. Read `licenses/SAM-LICENSE.txt` (or the current text on PyPI; section 8 lets
   Meta change the terms) and decide whether the codec in `sam31.py` is a
   derivative work of the SAM Materials.
2. If it is, or if in doubt: keep `licenses/SAM-LICENSE.txt` and this file in
   every distribution of the repository, and state in your credits that the
   codec follows Meta's `meta-sam-parser` (SAM License).
3. If it is not clearly outside the licence and you do not want that risk: set
   `SATWATCH_SAM_OUTLINES=0` in the deployed environment (box-only output), or
   replace the decoder with the official package as an optional import.
4. Check the licence's end-use limits (section 1.b.v: Trade Controls, ITAR,
   military or warfare uses) against how you describe and use the product.
