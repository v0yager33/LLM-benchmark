##!/bin/bash

HIP_COUNT=$(hy-smi | grep -cE "^[0-9]+")
HIP_COUNT=${HIP_COUNT:-0}  
MODEL_PATH="/root/public_data/model/admin/Qwen/QwQ-32B"

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

nohup vllm serve ${MODEL_PATH} --tensor-parallel-size ${HIP_COUNT} --max-model-len 8192 --enforce-eager --host 0.0.0.0 --disable-log-stats > vllm_serve.log 2>&1 &

sleep 5

if [ "$MODE" == "prod" ]; then
    # 在生产模式下，持续输出日志
    tail -f vllm_serve.log
    
elif [ "$MODE" == "dev" ]; then
    # 在开发模式下，只输出简短的日志并且在启动后结束
    tail -f vllm_serve.log | while read line; do
        if [[ "$line" =~ ^INFO.* && ${#line} -lt 200 ]]; then
            echo "$line"
        fi
        if [[ "$line" =~ Uvicorn\ running\ on.* ]]; then       
            pkill -f "tail -f vllm_serve.log"
            break
        fi
    done
    
    echo "服务已启动，可以继续运行后续单元格。"
fi
