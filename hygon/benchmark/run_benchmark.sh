#!/bin/bash

# 更新配置参数
LOG_DIR="/root/private_data/logs"
#SCRIPT_PATH="/root/benchmark/benchmark_serving.py"
SCRIPT_PATH="/root/private_data/benchmark/benchmark_serving.py"
MODEL_PATH="/root/public_data/model/admin/Qwen/QwQ-32B"
#MODEL_PATH="/root/public_data/model/admin/DeepSeek-R1-Distill-Llama-70B"
#SERVED_MODEL_NAME="/root/public_data/model/admin/Qwen/QwQ-32B"
SERVED_MODEL_NAME=my_model
ENDPOINT="/v1/chat/completions"
DATASET="random"
NUM_PROMPTS=6
PORT=8000
TRUST_REMOTE_CODE="--trust-remote-code"
PROCESSOR_TYPE="Hygon-K100"
PROCESSOR_NUMBER=4
BACKEND="VLLM"

mkdir -p "$LOG_DIR"

# 保持原有的循环顺序
CONCURRENCIES=(2 4 8 16)
INPUT_LENGTHS=(2048)
OUTPUT_LENGTHS=(256)

for concurrency in "${CONCURRENCIES[@]}"; do
    for input_len in "${INPUT_LENGTHS[@]}"; do
        for output_len in "${OUTPUT_LENGTHS[@]}"; do
            # 保持原始文件名格式和输出方式
            LOG_FILE="${LOG_DIR}/benchmark_${BACKEND}_${PROCESSOR_NUMBER}Unit_Len${input_len}_Con${concurrency}_$(date +'%Y%m%d_%H%M%S').log"
	    REQ_NUM=$(( NUM_PROMPTS * concurrency ))
	    echo "Total REQ : $REQ_NUM"
            
            echo "==============================================" | tee "$LOG_FILE"
            echo "Starting test: Backend=${BACKEND}, Input length=${input_len}, Concurrency=${concurrency}" | tee -a "$LOG_FILE"
            echo "Processor: ${PROCESSOR_TYPE}, Processor Number: ${PROCESSOR_NUMBER}" | tee -a "$LOG_FILE"
            echo "Log file: ${LOG_FILE}" | tee -a "$LOG_FILE"
            echo "==============================================" | tee -a "$LOG_FILE"
            
            # 更新为新的命令格式
            nohup python3 "$SCRIPT_PATH" \
                --served-model-name "$SERVED_MODEL_NAME" \
                --backend openai-chat \
                --model "$MODEL_PATH" \
                --endpoint "$ENDPOINT" \
                --dataset-name "$DATASET" \
                --num-prompts "$REQ_NUM" \
                --max-concurrency "$concurrency" \
                --random-input-len "$input_len" \
                --random-output-len "$output_len" \
                --port "$PORT" \
                $TRUST_REMOTE_CODE \
                2>&1 | tee -a "$LOG_FILE"
            
            if [ ${PIPESTATUS[0]} -ne 0 ]; then
                echo "[ERROR] Test failed!" | tee -a "$LOG_FILE"
                exit 1
            fi
            
            echo "Test completed." | tee -a "$LOG_FILE"
            sleep 60
        done    
    done
done
