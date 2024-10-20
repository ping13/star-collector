# RSS Feed for Personal Favorites and Bookmarks on Mastodon

## Overview

The `rss.php` script is a straightforward PHP tool designed to display my most
recent favorite and bookmarked toots on Mastodon. You can copy this script
along with the configuration file to your own hosting provider. As an example,
access the [RSS feed](http://ping13.net/mastodon/rss.php) for [`ping13@swiss.social`](https://swiss.social/@ping13).

## Using Large Language Models for Coding

The majority of the PHP script was developed using [aider](https://aider.chat),
which initially proved to be a pleasant experience. 

To set things up, I prefer utilizing [uv](https://astral.sh/uv) to use the latest
`aider-chat` release with all necessary dependencies. This can be achieved by
executing the following commands:

```
$ uv sync                     # uses pyproject.toml
$ uv run aider --architect
```

The `--architect` option was [recently
introduced](https://aider.chat/2024/09/26/architect.html), enabling pre-edit
reasoning for code. However, it is relatively costly, as it employs models from
both Anthropic and OpenAI.

The code was initially generated from scratch by `aider`, based on my prompts,
up until the point when aider failed to comprehend how pagination functions on
Mastodon for favorites and
[bookmarks](https://docs.joinmastodon.org/methods/bookmarks/).

I proceeded to debug the script on the command line and realized that the
`max_id` parameter does not correspond to the toot's `id`. Instead, it is an
"internal parameter," and it is necessary to parse the `Link` header from the
API response to determine the URL for the next page.
