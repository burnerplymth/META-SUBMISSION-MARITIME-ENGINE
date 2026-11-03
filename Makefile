# Shortcuts. The real work is in run.sh and the Dockerfile.
.PHONY: demo live docker
demo:            ## offline / recorded demo on http://127.0.0.1:8143
	./run.sh
live:            ## feeds and model may use the network and your keys (see README)
	./run.sh --live
docker:          ## the same demo in a container on http://localhost:8080
	docker compose up --build
