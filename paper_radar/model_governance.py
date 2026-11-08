# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Local-first cloud model readiness, transmission notices and bounded estimates.

Preflight is deterministic and OFFLINE. It never calls a provider, reads PDF
contents or exports a prompt. A test request is a separate explicit action.
Prices are user-supplied; unknown prices must stay unknown, not USD 0.
"""
from __future__ import annotations

from math import ceil, isfinite
from urllib.parse import urlsplit

WORKLOADS = {
    'summary': {'name':'中文论文速览', 'fields':['论文题名','论文摘要'], 'default_chars':4200, 'max_count':30, 'out_tokens':900,
                'description':'所选论文的题名和摘要会发送给模型；不会自动发送 PDF 全文。'},
    'ranking': {'name':'智能论文排序', 'fields':['候选论文题名','候选论文摘要'], 'default_chars':2800, 'max_count':30, 'out_tokens':300,
                'description':'候选论文题名与摘要会发送给模型打分；排序结果应人工检查。'},
    'planning': {'name':'生成检索关键词', 'fields':['用户输入的研究目标'], 'default_chars':700, 'max_count':1, 'out_tokens':700,
                 'description':'仅发送你输入的研究目标，不会读取本地论文库。'},
    'fulltext': {'name':'全文阅读卡片', 'fields':['PDF 中选择的原文片段'], 'default_chars':9000, 'max_count':3, 'out_tokens':1600,
                 'description':'可能向配置的第三方模型发送 PDF 原文片段；使用前需要主动确认。'},
    'experiments': {'name':'实验结果候选提取', 'fields':['PDF 中的实验章节、表格附近文本'], 'default_chars':9000, 'max_count':3, 'out_tokens':1600,
                    'description':'将选取的实验原文片段交给模型提取，结果必须与原始论文核对。'},
}


def _positive_rate(cfg, name):
    try:
        value=float(cfg.get(name, 0))
    except (ValueError, TypeError):
        return None
    return value if isfinite(value) and value>0 and value<=10000 else None


def describe_failure(message: str) -> dict:
    """Static, sanitized troubleshooting; avoid reflecting provider responses."""
    text=str(message or '').lower()
    cases=[
        ('key', ('401','403','api key','密钥','凭据'), '认证或权限异常','检查 API Key、服务商账号权限和模型访问资格。'),
        ('balance', ('402','余额','计费状态'), '余额或计费异常','到服务商控制台检查余额、账单和支付状态。'),
        ('rate_limit', ('429','限流','请求受限'), '请求频率受限','等待后重试，或降低每批论文数量。'),
        ('model', ('400','404','模型名称','未找到接口'), '模型或接口参数不匹配','核对服务商提供的模型 ID 与 API 地址。'),
        ('timeout', ('timeout','timed out','超时'), '连接超时','检查网络和代理，适当降低单次请求规模。'),
        ('network', ('连接','网络','dns','unreachable','urlerror'), '无法连接模型服务','检查网络、HTTPS 接口和服务商当前状态。'),
        ('response', ('json','格式','有效文本','empty'), '模型返回格式异常','确认接口兼容性，查看是否触发模型输出长度上限。'),
    ]
    for code, triggers, title, action in cases:
        if any(t in text for t in triggers):
            return {'code':code,'title':title,'action':action,'raw_redacted':True}
    return {'code':'unknown','title':'模型请求未完成','action':'打开任务日志查看错误类别，检查服务商控制台后重试。','raw_redacted':True}


def readiness(config: dict, *, key_present: bool) -> dict:
    provider=str(config.get('api_provider') or '')
    model=str(config.get('api_model') or '')
    try:
        endpoint=urlsplit(str(config.get('api_base_url') or '')).hostname or ''
        from .cloud_api import completion_endpoint
        completion_endpoint(str(config.get('api_base_url') or ''))
        valid_endpoint=True
    except (ValueError, OSError):
        valid_endpoint=False
    consent=bool(config.get('llm_data_consent', False))
    if not key_present:
        state, suggestion = 'key_missing','先在系统凭据管理器中保存模型 API Key。'
    elif not model or not valid_endpoint:
        state, suggestion = 'config_incomplete','请检查模型 ID 和 API 地址。'
    elif not consent:
        state, suggestion = 'awaiting_consent','连接配置已就绪。请确认数据传输授权后再启用科研内容调用。'
    else:
        state, suggestion = 'configured_unverified','模型配置就绪，可点击“测试连接”验证；测试可能产生少量费用。'
    return {'state':state,'configured':bool(key_present and model and valid_endpoint),
            'ready_for_research':state=='configured_unverified',
            'provider':provider,'model':model,'destination_host':endpoint,
            'api_key_saved':bool(key_present),'consent':consent,
            'connection_tested':False,'suggestion':suggestion,
            'notice':'本地配置检查不发起任何网络调用，也不代表模型接口已连通。'}


def estimate(config:dict, action:str='summary', *, count:int|None=None, chars_per_document:int|None=None) -> dict:
    if action not in WORKLOADS:
        raise ValueError('未知的模型任务类型。')
    spec=WORKLOADS[action]
    default_count=({'summary':config.get('ai_limit',5),'ranking':config.get('ai_rerank_limit',15),
                    'fulltext':3,'experiments':3}).get(action,1)
    try:
        n=int(default_count if count is None else count)
        chars=int(spec['default_chars'] if chars_per_document is None else chars_per_document)
    except (ValueError, TypeError):
        raise ValueError('任务规模必须是整数。') from None
    if not 1<=n<=spec['max_count'] or not 100<=chars<=20000:
        raise ValueError('任务规模超过估算允许范围。')
    # Chinese and technical prose tokenize differently. This is an intentionally
    # broad range; provider-side tokenization is not available before a request.
    prompt_overhead=380
    input_low=n*(ceil(chars/4)+prompt_overhead)
    input_high=n*(ceil(chars/1.1)+prompt_overhead)
    output_low=n*max(80, spec['out_tokens']//3)
    output_high=n*spec['out_tokens']
    input_rate=_positive_rate(config,'llm_input_usd_per_million')
    output_rate=_positive_rate(config,'llm_output_usd_per_million')
    price_known=input_rate is not None and output_rate is not None
    cost={'min':round((input_low*input_rate+output_low*output_rate)/1000000,6),
          'max':round((input_high*input_rate+output_high*output_rate)/1000000,6),
          'currency':'USD'} if price_known else None
    return {'action':action,'title':spec['name'],'documents':n,'assumed_chars_per_document':chars,
            'transmitted_fields':spec['fields'], 'transmission_notice':spec['description'],
            'prompt_tokens_range':[input_low,input_high],'output_tokens_range':[output_low,output_high],
            'estimated_cost':cost,'pricing_configured':price_known,
            'method':'rough_chars_to_tokens_range',
            'warning':'估算使用字符长度与手动单价，未执行真实模型调用；实际 Token、缓存、工具费、税费与供应商账单可能不同。'}
