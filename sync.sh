#!/bin/bash
while true; do
  rsync -avz "mybot:*.log" ~/Documents/bot_project/
  sleep 60
done
