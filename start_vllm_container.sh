#!/bin/bash

####################################
# 默认配置
####################################
CONTAINER_NAME="vllm-kxdu"
DOCKER_IMAGE="vllm/vllm-openai:v0.7.3"
GPU_DEVICES="1"
HOST_PORT="10018"
MODEL_PATH="/storagedata/common/models/Qwen/Qwen2_5-0_5B-Instruct"
MEMORY_UTIL="0.2"
MAX_WAIT_SECONDS=300
HOST_PATH="/storagedata"        # 宿主机路径
CONTAINER_PATH="/storagedata"   # 容器内路径

####################################
# 使用说明
####################################
usage() {
    echo "用法: $0 [选项]"
    echo "选项:"
    echo "  -n, --name        容器名称 (默认: $CONTAINER_NAME)"
    echo "  -i, --image       Docker镜像 (默认: $DOCKER_IMAGE)"
    echo "  -g, --gpus        GPU设备ID (默认: $GPU_DEVICES)"
    echo "  -p, --port        主机端口 (默认: $HOST_PORT)"
    echo "  -m, --model       模型路径 (默认: $MODEL_PATH)"
    echo "  -u, --util        GPU内存利用率 (默认: $MEMORY_UTIL)"
    echo "  -h, --host-path   宿主机路径 (默认: $HOST_PATH)"
    echo "  -c, --container-path 容器内路径 (默认: $CONTAINER_PATH)"
    echo "  -w, --wait        最大等待时间(秒) (默认: $MAX_WAIT_SECONDS)"
    echo "  -h, --help        显示帮助信息"
    exit 0
}

####################################
# 参数解析
####################################
while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--name)
            CONTAINER_NAME="$2"
            shift 2
            ;;
        -i|--image)
            DOCKER_IMAGE="$2"
            shift 2
            ;;
        -g|--gpus)
            GPU_DEVICES="$2"
            shift 2
            ;;
        -p|--port)
            HOST_PORT="$2"
            shift 2
            ;;
        -m|--model)
            MODEL_PATH="$2"
            shift 2
            ;;
        -u|--util)
            MEMORY_UTIL="$2"
            shift 2
            ;;
        -h|--host-path)
            HOST_PATH="$2"
            shift 2
            ;;
        -c|--container-path)
            CONTAINER_PATH="$2"
            shift 2
            ;;
        -w|--wait)
            MAX_WAIT_SECONDS="$2"
            shift 2
            ;;
        --help)
            usage
            ;;
        *)
            echo "未知选项: $1"
            usage
            exit 1
            ;;
    esac
done

####################################
# 容器清理
####################################
clean_container() {
    echo "[1/3] 检查历史容器: $CONTAINER_NAME"
    if docker ps -aq --filter "name=^${CONTAINER_NAME}$" | grep -q .; then
        echo "-> 发现已有容器，正在清理..."
        if ! docker rm -f "$CONTAINER_NAME" >/dev/null; then
            echo "错误：容器清理失败！"
            exit 1
        fi
        echo "-> 容器清理完成"
    else
        echo "-> 无历史容器"
    fi
}

####################################
# 服务健康检查
####################################
check_service_health() {
    local start_time=$(date +%s)
    local end_time=$((start_time + MAX_WAIT_SECONDS))
    local check_interval=5
    
    echo "[3/3] 等待服务启动（最长${MAX_WAIT_SECONDS}秒）..."
    echo "-> 测试地址：http://localhost:${HOST_PORT}/health"
    
    while [ $(date +%s) -lt $end_time ]; do
        if curl -sSf "http://localhost:${HOST_PORT}/health" >/dev/null; then
            local elapsed=$(( $(date +%s) - start_time ))
            echo -e "\n-> 服务启动成功！（耗时 ${elapsed}秒）"
            echo "-> 完整访问地址：http://localhost:${HOST_PORT}"
            return 0
        fi
        
        echo -n "."
        sleep $check_interval
    done
    
    echo -e "\n错误：服务启动超时！"
    return 1
}

####################################
# 主流程
####################################
clean_container

echo "[2/3] 启动容器并运行服务..."
if ! docker run -itd \
    --name "$CONTAINER_NAME" \
    --gpus all \
    -v "${HOST_PATH}:${CONTAINER_PATH}" \
    -p "$HOST_PORT":8000 \
    --ipc=host \
    --env CUDA_VISIBLE_DEVICES="$GPU_DEVICES" \
    "$DOCKER_IMAGE" \
    --model "$MODEL_PATH" \
    --host 0.0.0.0 \
    --port 8000 \
    --gpu-memory-utilization "$MEMORY_UTIL"; then
    echo "错误：容器启动失败！"
    exit 1
fi

# 健康检查
if ! check_service_health; then
    echo "=============================================="
    echo "故障诊断信息："
    echo "1. 容器日志："
    docker logs --tail 50 "$CONTAINER_NAME"
    echo "----------------------------------------------"
    echo "2. GPU状态（设备 $GPU_DEVICES）："
    nvidia-smi -i "$GPU_DEVICES"
    echo "----------------------------------------------"
    echo "3. 端口检查："
    netstat -tulnp | grep "$HOST_PORT" || echo "端口未监听"
    echo "=============================================="
    exit 1
fi

exit 0
