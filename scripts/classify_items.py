#!/usr/bin/env python3
import json, sys
from pathlib import Path
RULES = {
    '家居装修与收纳': ['家居','装修','餐边柜','镜柜','台盆柜','厨房','豪宅','收纳','客厅','卧室'],
    '穿搭发型与品味': ['穿搭','时尚','男士','香水','老钱风','西装','ootd','vogue','chanel'],
    '滑雪': ['滑雪','单板','雪场','固定器','换刃','casi'],
    '体态纠正与康复': ['走姿','呼吸','康复','梨状肌','崴脚','一字马','肚腩'],
    '运动训练与体态': ['硬拉','训练','腿部力量','跟练','跑步动作'],
    '效率系统与AI': ['app','小组件','收藏夹批量管理','口播神器','科研写作','效率','ai'],
    '摄影审美与创作': ['剪辑','配乐','徕卡','字体','故事感','画线'],
    '思考与成长': ['成长','松弛感','西西弗','心智成熟','探索新奇'],
}

def main():
    src = Path(sys.argv[1]); out = Path(sys.argv[2]); items = json.loads(src.read_text(encoding='utf-8'))
    result = []
    for item in items:
        blob = '
'.join([item.get('title',''), item.get('desc',''), ' '.join(item.get('tags',[])), item.get('user','')]).lower()
        board, reasons = '杂项灵感', []
        for name, words in RULES.items():
            hits = [w for w in words if w.lower() in blob]
            if hits:
                board, reasons = name, hits
                break
        result.append({'id': item.get('id'), 'title': item.get('title'), 'target_board': board, 'confidence': 'high' if reasons else 'low', 'reason': reasons, 'review_state': 'pending' if not reasons else 'classified'})
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'count': len(result), 'output': str(out)}, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
