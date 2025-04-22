#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import csv
from datetime import datetime

# 配置路径
LOG_DIR = "./log"
OUTPUT_DIR = "./output/"
OUTPUT_CSV = os.path.join(OUTPUT_DIR, "性能测试汇总.csv")

# 确保输出目录存在
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 定义提取模式
PATTERNS = {
    "Backend": re.compile(r"Backend\s*=\s*([\w-]+)"), 
    "Processor": re.compile(r"Processor\s*[:=]\s*([^\s,]+)"), 
    "Processor_Number": re.compile(r"Processor Number\s*[:=]\s*(\d+)"),
    "input_length": re.compile(r"Input length\s*[:=]\s*(\d+)"),
    "Output_length": re.compile(r"Output length\s*[:=]\s*(\d+)"),
    "concurrency": re.compile(r"Concurrency\s*[:=]\s*(\d+)"),
    "successful_requests": re.compile(r"Successful requests\s*[:=]\s+(\d+)"),
    "duration": re.compile(r"Duration\s*[:=]\s+([\d.]+)\s*s"),
    "input_tokens": re.compile(r"Input tokens\s*[:=]\s+(\d+)"),
    "output_tokens": re.compile(r"Output tokens\s*[:=]\s+(\d+)"),
    "request_throughput": re.compile(r"Request throughput\s*[:=]\s+([\d.]+)\s+req/s"),
    "token_throughput": re.compile(r"Token throughput\s*[:=]\s+([\d.]+)\s+tok/s"),
    "ttft_mean": re.compile(r"Time To First Token Metrics.*?Mean\s*[:=]\s+([\d.]+)\s+ms", re.DOTALL),
    "ttft_median": re.compile(r"Time To First Token Metrics.*?Median\s*[:=]\s+([\d.]+)\s+ms", re.DOTALL),
    "ttft_p99": re.compile(r"Time To First Token Metrics.*?P99\s*[:=]\s+([\d.]+)\s+ms", re.DOTALL),
    "tpot_mean": re.compile(r"Time Per Output Token Metrics.*?Mean\s*[:=]\s+([\d.]+)\s+ms", re.DOTALL),
    "tpot_median": re.compile(r"Time Per Output Token Metrics.*?Median\s*[:=]\s+([\d.]+)\s+ms", re.DOTALL),
    "tpot_p99": re.compile(r"Time Per Output Token Metrics.*?P99\s*[:=]\s+([\d.]+)\s+ms", re.DOTALL),
    "itl_mean": re.compile(r"Inter-Token Latency Metrics.*?Mean\s*[:=]\s+([\d.]+)\s+ms", re.DOTALL),
    "itl_median": re.compile(r"Inter-Token Latency Metrics.*?Median\s*[:=]\s+([\d.]+)\s+ms", re.DOTALL),
    "itl_p99": re.compile(r"Inter-Token Latency Metrics.*?P99\s*[:=]\s+([\d.]+)\s+ms", re.DOTALL),
    "e2e_mean": re.compile(r"End-to-End Latency Metrics.*?Mean\s*[:=]\s+([\d.]+)\s+ms", re.DOTALL),
    "e2e_median": re.compile(r"End-to-End Latency Metrics.*?Median\s*[:=]\s+([\d.]+)\s+ms", re.DOTALL),
    "e2e_p99": re.compile(r"End-to-End Latency Metrics.*?P99\s*[:=]\s+([\d.]+)\s+ms", re.DOTALL),
    "add_mean": re.compile(r"Mean time except first token\s*[:=]\s+([\d.]+)\s+ms"),
    "add_median": re.compile(r"Median time except first token\s*[:=]\s+([\d.]+)\s+ms"),
    "add_p99": re.compile(r"P99 time except first token\s*[:=]\s+([\d.]+)\s+ms")
}

# 默认值设置
DEFAULT_VALUES = {
    "Backend": "VLLM",
    "Output_length": 1024,
    "Processor": "Hygon-K100",
    "Processor_Number": 2
}


def parse_log_file(filepath):
    """解析单个日志文件"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    result = {}
    for key, pattern in PATTERNS.items():
        match = pattern.search(content)
        if match:
            try:
                # 提取匹配到的值并去除前后空格
                value = match.group(1).strip()

                # 判断字段是否需要转换为数值类型
                if key in ["input_length", "Output_length", "concurrency", "successful_requests",
                           "duration", "input_tokens", "output_tokens", "request_throughput", "token_throughput"]:
                    # 数值字段：尝试将值转换为整数或浮点数
                    result[key] = float(value) if '.' in value else int(value)
                else:
                    # 非数值字段（如 Backend 和 Processor）：保持为字符串
                    result[key] = value
            except ValueError:
                # 如果转换失败，记录警告并设置为 None
                result[key] = None
                print(f"Warning: Failed to convert field '{key}' in file '{filepath}'")
        else:
            # 如果未匹配到，使用默认值（如果存在）
            if key in DEFAULT_VALUES:
                result[key] = DEFAULT_VALUES[key]
                print(f"Debug: Field '{key}' not found in file '{filepath}', using default value: {result[key]}")
            else:
                result[key] = None  # 如果未匹配到且没有默认值，设置为 None
                print(f"Debug: Field '{key}' not found in file '{filepath}', no default value available.")

    # 添加文件名信息
    result['filename'] = os.path.basename(filepath)
    return result


def main():
    # 收集所有日志文件
    log_files = [f for f in os.listdir(LOG_DIR) if  f.endswith('.log')]

    # 解析所有文件
    all_results = []
    for log_file in log_files:
        try:
            result = parse_log_file(os.path.join(LOG_DIR, log_file))
            all_results.append(result)
        except Exception as e:
            print(f"解析失败 {log_file}: {e}")

    # 按输入长度和并发数排序，处理 None 值
    all_results.sort(key=lambda x: ((x['input_length'] or 0), (x['concurrency'] or 0)))

    # 中文列名
    fieldnames = [
        '推理框架',
        '加速卡类型',
        '加速卡数',
        '输入长度',
        '输出长度',
        '并发数',
        '成功请求数',
        '总耗时(s)',
        '输入token数',
        '输出token数',
        '总输出Token速率(req/s)',
        '输出吞吐量(tok/s)',
        '请求吞吐量(req/s)',
        'Token吞吐量(tok/s)',
        '首Token时间均值(ms)',
        '首Token时间中位数(ms)',
        '首Token时间P99(ms)',
        '每输出Token时间均值(ms)',
        '每输出Token时间中位数(ms)',
        '每输出Token时间P99(ms)',
        'Token间延迟均值(ms)',
        'Token间延迟中位数(ms)',
        'Token间延迟P99(ms)',
        '端到端延迟均值(ms)',
        '端到端延迟中位数(ms)',
        '端到端延迟P99(ms)',
        '其他Token生成时间均值(ms)',
        '其他Token生成时间中位数(ms)',
        '其他Token生成时间P99(ms)'
    ]

    # 写入CSV（使用Excel兼容格式）
    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8-sig') as csvfile:  # utf-8-sig解决Excel中文乱码
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        for result in all_results:
            # 计算输出Token速率，确保分母不为零
            output_token_rate = (
                round(result['output_tokens'] / result['duration'], 2)
                if isinstance(result['output_tokens'], (int, float)) and
                   isinstance(result['duration'], (int, float)) and
                   result['duration'] > 0
                else 0
            )
            writer.writerow({
                '推理框架': result['Backend'],
                '加速卡类型': result['Processor'],
                '加速卡数': result['Processor_Number'],
                '输入长度': result['input_length'],
                '输出长度': result['Output_length'],
                '并发数': result['concurrency'],
                '成功请求数': result['successful_requests'],
                '总耗时(s)': result['duration'],
                '输入token数': result['input_tokens'],
                '输出token数': result['output_tokens'],
                '请求吞吐量(req/s)': result['request_throughput'],
                #'总输出Token速率(tok/s)': output_token_rate,
                'Token吞吐量(tok/s)': result['token_throughput'],
                '首Token时间均值(ms)': result['ttft_mean'],
                '首Token时间中位数(ms)': result['ttft_median'],
                '首Token时间P99(ms)': result['ttft_p99'],
                '每输出Token时间均值(ms)': result['tpot_mean'],
                '每输出Token时间中位数(ms)': result['tpot_median'],
                '每输出Token时间P99(ms)': result['tpot_p99'],
                'Token间延迟均值(ms)': result['itl_mean'],
                'Token间延迟中位数(ms)': result['itl_median'],
                'Token间延迟P99(ms)': result['itl_p99'],
                '端到端延迟均值(ms)': result['e2e_mean'],
                '端到端延迟中位数(ms)': result['e2e_median'],
                '端到端延迟P99(ms)': result['e2e_p99'],
                '其他Token生成时间均值(ms)': result['add_mean'],
                '其他Token生成时间中位数(ms)': result['add_median'],
                '其他Token生成时间P99(ms)': result['add_p99']
            })

    print(f"生成报告: {OUTPUT_CSV}")
    print(f"已处理日志文件: {len(all_results)} 个")


if __name__ == "__main__":
    main()
