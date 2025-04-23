#!/bin/bash
echo ">> Setting up Trading Agentic Core Deployment"

# Copy env template
cp .env.template .env

# Build Docker container
docker-compose build

# Launch system
docker-compose up