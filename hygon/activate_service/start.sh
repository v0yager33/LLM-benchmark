#!/bin/bash


HIP_COUNT=$(hy-smi | grep -cE "^[0-9]+")
HIP_COUNT=${HIP_COUNT:-0}  
MODEL_PATH="/root/public_data/model/admin/Qwen/QwQ-32B"
#MODEL_PATH="/root/public_data/model/admin/DeepSeek-R1-Distill-Llama-70B/"
SERVERD_MODEL_NAME=my_model


MODE="dev"
if [[ "$1" == "--mode" ]]; then
    if [[ "$2" == "dev" || "$2" == "prod" ]]; then
        MODE="$2"
    else
        echo "无效的模式: $2. 请使用 --mode dev 或 --mode prod."
        exit 1
    fi
fi

if [ $HIP_COUNT -lt 2 ]; then
    echo -e "您需要至少\e[1m\e[31m 2 \e[0m张异构加速卡AI，当前数量: \e[1m\e[34m ${HIP_COUNT} \e[0m张，请重新选择并创建"  
    exit 1
fi

if [ ! -d "$MODEL_PATH" ]; then
    echo -e "\e[1m\e[31m错误：模型路径不存在，请选择015、020或021组异构加速卡资源。\e[0m"
    exit 1
fi

vllm serve ${MODEL_PATH} --tensor-parallel-size ${HIP_COUNT} \
	--max-model-len 8192 \
	--served-model-name ${SERVERD_MODEL_NAME} \
	--enforce-eager \
	--gpu-memory-utilization 0.95 \
	--enable-prefix-caching \
	--max-num-seqs 8 \
	--enable-chunked-prefill  \
	--host 0.0.0.0 --disable-log-stats 
#nohup vllm serve ${MODEL_PATH} --tensor-parallel-size ${HIP_COUNT} --max-model-len 8192 --served-model-name ${SERVERD_MODEL_NAME} --enforce-eager --gpu-memory-utilization 0.95 --swap-space 8 --enable-prefix-caching  --host 0.0.0.0 --disable-log-stats > vllm_serve.log 2>&1 &
