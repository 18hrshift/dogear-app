"""Voices from OpenAI-compatible speech services for Dogear: a voice engine plugin
(Apache-2.0).

Any service or server that takes `POST <base_url>/audio/speech` the way OpenAI's does.
Dogear finds it through the `dogear.engines` entry point in this package's pyproject.toml;
Dogear itself never imports it. A service on another computer is sent the text it speaks,
so Dogear uses it only when the user allows that, and says so first. See README.md.
"""
