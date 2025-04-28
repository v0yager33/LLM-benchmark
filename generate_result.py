#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import csv
from datetime import datetime

# 配置路径
LOG_DIR = "./"
OUTPUT_DIR = "./"
OUTPUT_CSV = os.path.join(OUTPUT_DIR, "性能测试汇总.csv")

# 确保输出目录存在
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 定义提取模式
PATTERNS = {
    "backend": re.compile(r"Backend\s*=\s*([\w-]+)"),
    "accelerator_type": re.compile(r"Accelerator:\s*([\w-]+)"),
    "accelerator_number": re.compile(r"Accelerator Number: (\d+)"),
    "model_name": re.compile(r"Model=(\S+)"),
    "input_length": re.compile(r"Input length=(\d+)"),
    "output_length": re.compile(r"Output length=(\d+)"),
    "concurrency": re.compile(r"Concurrency=(\d+)"),
    "request_rounds": re.compile(r"Rounds=(\d+)"),
    "successful_requests": re.compile(r"Successful requests:\s+(\d+)"),
    "total_requests": re.compile(r"num_prompts=(\d+)"),
    "duration": re.compile(r"Duration:\s+([\d.]+)\s+s"),
    "input_tokens": re.compile(r"Input tokens:\s+(\d+)"),
    "output_tokens": re.compile(r"Output tokens:\s+(\d+)"),
    "request_throughput": re.compile(r"Request throughput:\s+([\d.]+)\s+req/s"),
    "token_throughput": re.compile(r"Token throughput:\s+([\d.]+)\s+tok/s"),
    "output_throughput": re.compile(r"Output throughput:\s+([\d.]+)\s+tok/s"),
    "ttft_mean": re.compile(r"Time To First Token Metrics.*?Mean:\s+([\d.]+)\s+ms", re.DOTALL),
    "ttft_median": re.compile(r"Time To First Token Metrics.*?Median:\s+([\d.]+)\s+ms", re.DOTALL),
    "ttft_p99": re.compile(r"Time To First Token Metrics.*?P99:\s+([\d.]+)\s+ms", re.DOTALL),
    "tpot_mean": re.compile(r"Time Per Output Token Metrics.*?Mean:\s+([\d.]+)\s+ms", re.DOTALL),
    "tpot_median": re.compile(r"Time Per Output Token Metrics.*?Median:\s+([\d.]+)\s+ms", re.DOTALL),
    "tpot_p99": re.compile(r"Time Per Output Token Metrics.*?P99:\s+([\d.]+)\s+ms", re.DOTALL),
    "itl_mean": re.compile(r" Inter-Token Latency Metrics.*?Mean:\s+([\d.]+)\s+ms", re.DOTALL),
    "itl_median": re.compile(r" Inter-Token Latency Metrics.*?Median:\s+([\d.]+)\s+ms", re.DOTALL),
    "itl_p99": re.compile(r" Inter-Token Latency Metrics.*?P99:\s+([\d.]+)\s+ms", re.DOTALL),
    "e2e_mean": re.compile(r"End-to-End Latency Metrics.*?Mean:\s+([\d.]+)\s+ms", re.DOTALL),
    "e2e_median": re.compile(r"End-to-End Latency Metrics.*?Median:\s+([\d.]+)\s+ms", re.DOTALL),
    "e2e_p99": re.compile(r"End-to-End Latency Metrics.*?P99:\s+([\d.]+)\s+ms", re.DOTALL),
    "add_mean": re.compile(r"Mean time except first token:\s+([\d.]+)\s+ms"),
    "add_median": re.compile(r"Median time except first token:\s+([\d.]+)\s+ms"),
    "add_p99": re.compile(r"P99 time except first token:\s+([\d.]+)\s+ms")
}


def parse_log_file(filepath):
    """解析单个日志文件"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            if lines and lines[-1].strip() != 'Test completed.':
                print(f"警告: 跳过文件 {filepath}，该测试未成功完成")
                return None

            content = ''.join(lines)

        result = {}
        for key, pattern in PATTERNS.items():
            match = pattern.search(content)
            if match:
                # 非数值字段直接保存为字符串
                if key in ["backend", "accelerator_type", "model_name"]:
                    result[key] = match.group(1)
                else:
                    # 数值字段根据内容转换为整数或浮点数
                    try:
                        result[key] = float(match.group(1)) if '.' in match.group(1) else int(match.group(1))
                    except ValueError as e:
                        print(f"字段 {key} 的值无法转换为数字: {match.group(1)}")
                        result[key] = None
            else:
                result[key] = None

        # 计算请求成功率
        if result['successful_requests'] is not None and result['total_requests'] is not None:
            result['request_success_rate'] = result['successful_requests'] / result['total_requests'] * 100
        else:
            result['request_success_rate'] = None

        # 添加文件名信息
        result['filename'] = os.path.basename(filepath)
        return result
    except Exception as e:
        print(f"解析失败 {filepath}: {e}")
        return None


def main():
    # 收集所有日志文件
    log_files = [f for f in os.listdir(LOG_DIR) if f.startswith('benchmark_') and f.endswith('.log')]
    read_file_count = len(log_files)
    skipped_file_count = 0
    # 解析所有文件
    all_results = []
    for log_file in log_files:
        result = parse_log_file(os.path.join(LOG_DIR, log_file))
        if result:
            all_results.append(result)
        else:
            skipped_file_count += 1

    # 按输入长度和并发数排序
    all_results.sort(key=lambda x: (x['input_length'], x['concurrency']))

    # 中文列名
    fieldnames = [
        '推理框架',
        '模型名称',
        '加速卡类型',
        '加速卡数',
        '输入长度',
        '输出长度',
        '并发数',
        '成功请求数',
        '总请求数',
        '请求轮数',
        '请求成功率',
        '总耗时(ms)',
        '输入token数',
        '输出token数',
        '请求吞吐量(req/s)',
        'Token吞吐量(tok/s)',
        '输出吞吐量(tok/s)',
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
            writer.writerow({
                '推理框架': result['backend'],
                '模型名称': result['model_name'],
                '加速卡类型': result['accelerator_type'],
                '加速卡数': result['accelerator_number'],
                '输入长度': result['input_length'],
                '输出长度': result['output_length'],
                '并发数': result['concurrency'],
                '成功请求数': result['successful_requests'],
                '总请求数': result['total_requests'],
                '请求轮数': result['request_rounds'],
                '请求成功率': result['request_success_rate'],
                '总耗时(ms)': result['duration'],
                '输入token数': result['input_tokens'],
                '输出token数': result['output_tokens'],
                '请求吞吐量(req/s)': result['request_throughput'],
                'Token吞吐量(tok/s)': result['token_throughput'],
                '输出吞吐量(tok/s)': result['output_throughput'],
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
    print(f"已处理日志文件数: {len(all_results)} 个")
    print(f"读取文件数: {read_file_count} 个")
    print(f"跳过文件数: {skipped_file_count} 个")


if __name__ == "__main__":
    main()