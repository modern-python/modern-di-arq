# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this
repository.

## Project Overview

`modern-di-arq` is an [arq](https://arq-docs.helpmanual.io) adapter over
[`modern-di`](https://github.com/modern-python/modern-di); [`CONTEXT.md`](CONTEXT.md) opens with
what it does and owns the vocabulary — read it before naming a concept in code, a test name, or an
issue title. arq's **task** and its **job** are the pair that gets conflated here, and `CONTEXT.md`
pins the difference. This is one of that project's integrations, each of which lives in a separate
repository and ships as a separate PyPI package.

## Commands

`just` (task runner) and `uv` (package manager). The [`justfile`](justfile) is the source of truth —
`just --list`, or read it. The one thing it does not say: the suite needs a real Redis
(`just redis-up`, or set `REDIS_URL`) — arq has no in-memory broker, so the tests that exercise
the full path drive a real burst worker.

## Architecture

All implementation is `modern_di_arq/main.py`, short enough to read whole. Read it.

## Workflow

Every link in `README.md` must be absolute: `https://github.com/modern-python/<repo>/blob/main/<path>`,
or `.../tree/main/<path>` for a directory. Never a relative path: `README.md` is also the PyPI long
description, and PyPI does not rewrite relative links, so a relative one 404s on the package page.
