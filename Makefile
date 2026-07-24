.PHONY: run update-prod

PORT ?= 8080

run:
	python3 -m http.server $(PORT)

update-prod:
	python3 scripts/deploy_ftp.py
