# Israeli Finance News Aggregator

Combines headlines from Calcalist, Bizportal, Globes, and TheMarker into one
RSS feed, rebuilt every 30 minutes by GitHub Actions and published free via
GitHub Pages. Nothing runs on your own machine.

## Your feed URL (after setup)
```
https://YOUR_USERNAME.github.io/israeli-finance-feed/feed.xml
```
Paste that into Feedly, Inoreader, NetNewsWire, or any RSS reader.

## Setup: see the numbered steps your assistant gave you.

## If one site stops appearing
Open the Actions tab → click the latest run → read the log. It prints, per site,
how many headlines were parsed or why it failed. Most likely cause is a site
blocking the runner IP; the fix is a one-line change to that site's parser or
adding a proxy step.
