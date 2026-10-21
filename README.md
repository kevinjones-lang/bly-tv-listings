# Bly TV Listings

Full-screen, auto-advancing slideshow of The Bly Team's listings for the office TV.

* Data: Google Sheet "Bly TV Listings Feed" (1rFoocO92dt3iqswf6IH6-oML7ZfhzdOAcVli5ujVJYU). Columns A-F (from the Closings Board "Listings" tab) hold Address, List Price, Bed/Bath, Sq Ft / Lot Size, DOM and Vacant/Occupied; columns H-J (from the "Closings" tab) hold Pending Address, Pending Price and Expected Close.
* The page fetches the feed's CSV when it loads and again each time the loop returns to the title slide. If the fetch fails, it keeps showing the last good data (it also has a built-in snapshot as a fallback).
* Keys: left/right arrows, Space, Home, F = fullscreen (double-click also toggles it).
* URL options: `?secs=10` sets seconds per slide; `?feed=<csv url>` uses a different CSV, e.g. a "Publish to web" CSV link.
