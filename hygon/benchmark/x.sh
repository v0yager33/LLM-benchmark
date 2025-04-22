curl http://0.0.0.0:8000/v1/chat/completions \
     -H "Content-Type: application/json" \
     -d '{
           "model": "DeepSeek-R1-Distill-Llama-70B",
           "messages":[
             {"role":"system","content":"You are a helpful assistant."},
             {"role":"user","content":"帮我写一段诗，主题是春天。"}
           ]
         }'

