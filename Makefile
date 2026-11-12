.PHONY: setup fetch-rcn fetch-rcn-sample parse-rcn eda rcn-all boundaries gtfs osm gus context context-all features features-report test lint fmt

setup:
	uv sync

fetch-rcn-sample:
	uv run rcn fetch-rcn --sample 4

fetch-rcn:
	uv run rcn fetch-rcn

parse-rcn:
	uv run rcn parse-rcn

eda:
	uv run rcn eda-rcn

rcn-all: fetch-rcn parse-rcn eda

boundaries:
	uv run rcn fetch-boundaries

gtfs:
	uv run rcn fetch-gtfs

osm:
	uv run rcn fetch-osm

gus:
	uv run rcn fetch-gus

context:
	uv run rcn context-report

context-all: boundaries gtfs osm context

features:
	uv run rcn build-features

features-report:
	uv run rcn features-report

test:
	uv run pytest

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

fmt:
	uv run ruff format src tests
	uv run ruff check --fix src tests
