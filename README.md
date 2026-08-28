# PRAMAAN

**PRAMAAN** is an AI-assisted Legal Metrology compliance engine for packaged
commodities, built by team **MetaVision** for Smart India Hackathon 2026
(problem statement SIH26034, Ministry of Consumer Affairs, Food & Public
Distribution). Most label scanners check *presence* — is the MRP printed?
PRAMAAN checks *conformity* — is it printed in the manner and character size
Rule 7 of the Legal Metrology (Packaged Commodities) Rules, 2011 prescribes.
A vision-language model reads the declaration panel into structured fields;
a deterministic rule engine, never the model, issues the compliance verdict;
and an ArUco-calibrated OpenCV pipeline measures printed character height in
millimetres from an ordinary photograph.

See `CLAUDE.md` for the standing engineering rules and `BUILD_PLAN.md` for
the phased execution plan and current status.
