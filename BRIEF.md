# BRIEF — Fixgraph results page

- **Subject:** single-file HTML results page for the Fixgraph hackathon entry.
  Shows the benchmark chart (4 arms × 3 metrics over 5 rounds), one worked
  lookup with its evidence path + the Cypher that produced it, and the
  go-to-market summary.
- **Audience:** hackathon judges — engineers who will skim in under a minute
  and click the evidence if skeptical.
- **Emotional goal:** "this team measured something real" — competence,
  evidence, restraint.
- **Must NOT feel like:** an AI-generated landing page, a pitch deck, a
  crypto dashboard.
- **Constraints:** single HTML file, works from file://, no build step,
  chart is an embedded PNG from the benchmark run (not re-rendered).
