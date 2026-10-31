# Dogear in Docker

For libraries, schools, and volunteer groups that want Dogear's web page on a shared
computer or server. The image holds Dogear, the programs it runs (ffmpeg, eSpeak NG, and
Tesseract with its English language pack, for reading scanned PDFs), and the default
voice, so once it is built it works with no internet connection.

## Build the image

You need Docker with BuildKit, its default builder since Docker 23. (Ubuntu's own
`docker.io` package leaves BuildKit out: also install `docker-buildx`.)

```bash
git clone https://github.com/18hrshift/dogear-app
cd dogear-app
docker build -t dogear .
```

Building downloads the voice (about 340 MB) and the programs. The image takes about 1.9 GB
of disk.

## Run the web page

```bash
docker run -d --name dogear -p 8765:8765 -v dogear-data:/data --restart unless-stopped dogear
docker logs dogear
```

The log shows the access link. On the computer running Docker, open it:
`http://127.0.0.1:8765/start?key=...`. On other computers and phones, use the same link
with this computer's network address in place of `127.0.0.1`, such as
`http://192.168.1.20:8765/start?key=...`, as the log says. (Inside the container, Dogear
can find only the container's own address, which other devices can't reach, so the
image's settings turn off `show_network_address` and the log shows no second link.)

`-p 8765:8765` lets every device on the network reach the page. To keep it to this
computer only, use `-p 127.0.0.1:8765:8765` instead.

Anyone who has the link can use Dogear, and the connection isn't encrypted. Keep it on a
network you trust and never open it to the internet. To offer it more widely, put it
behind a web server that adds HTTPS and its own sign-in.

To stop it: `docker stop dogear`. A book being made stops too, and its page has a Start
again button that carries on where it stopped.

## Where things are kept

Everything is on the `/data` volume (`dogear-data` above):

- `/data/web`: uploaded books, everything made from them, and the access key. Only
  the user Dogear runs as (1000) can open them.
- `/data/cache`: every sentence spoken so far, so a stopped book carries on where it
  stopped. Safe to delete.

Keeping the volume keeps the same access link working. To make a new key, for example
after the link was shared by mistake:

```bash
docker exec dogear rm /data/web/access-key.txt
docker restart dogear
docker logs dogear
```

## The command line in the container

Every `dogear` command works. Mount the folder with your book at `/data`:

```bash
docker run --rm -v "$PWD:/data" dogear make /data/mybook.epub --format m4b,daisy -o /data
```

Dogear runs as user 1000 in the container, so the folder must be writable by that user.
If yours belongs to another user, add `--user "$(id -u):$(id -g)"`. The speech cache goes
in a `cache` folder there.

## Settings

The image reads its settings from `/etc/dogear/config.toml` (it is
[`config.toml`](config.toml) in this folder). To change more settings, write your own file
with the same `[models]`, `[render]`, and `[web]` lines plus your changes, and mount it
over that one:

```bash
docker run -d --name dogear -p 8765:8765 -v dogear-data:/data \
  -v "$PWD/my-settings.toml:/etc/dogear/config.toml:ro" dogear
```

The project's [README](../README.md) explains the settings.

## What's inside

- Dogear (Apache-2.0) on Python 3.13 and Debian 13 (the official `python:3.13-slim-trixie`
  image).
- From Debian, run as separate programs: ffmpeg (GPL), eSpeak NG (GPL-3.0), and Tesseract
  with English (Apache-2.0). Debian publishes the source of each.
- The Kokoro voice (Apache-2.0) and spaCy's English model (MIT), checked against their
  pinned SHA-256 while building.
- Not included: Calibre, which Dogear uses for Kindle books. It would more than double
  the image. Convert Kindle books to EPUB first, or use Dogear outside Docker for them.
