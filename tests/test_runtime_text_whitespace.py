"""Exercise the actual browser collector's visibility predicate with DOM measurements."""
import json
import subprocess
from pathlib import Path


def test_collapsed_separator_survives_but_hidden_and_missing_text_do_not() -> None:
    script = (Path(__file__).parents[1] / 'skills/visual-debug/scripts/runtime-text-sequence-check.sh').read_text()
    predicate = script.split('  const isRenderedText = ', 1)[1].split('  const blockFor = ', 1)[0].strip().removesuffix(';')
    js = '''
    const rect = {width:100,height:20,left:0,right:100,top:0,bottom:20};
    const parent = {tagName:'SPAN',closest:()=>false,parentElement:null,getClientRects:()=>[rect]};
    const root = {}; const skippedTags = new Set();
    const isHidden = el => !!el.hidden;
    const hasVisibleTextPaint = () => true;
    const isOccluded = () => false;
    const innerWidth=1440, innerHeight=900,scrollY=0;
    const document = {documentElement:{scrollHeight:1000},createRange:()=>({selectNodeContents:()=>{},getClientRects:()=>[],detach:()=>{}})};
    const check = ''' + predicate + ''';
    const results = [check({parentElement:parent,nodeValue:' '}, true), check({parentElement:parent,nodeValue:'missing'})];
    parent.hidden=true;
    results.push(check({parentElement:parent,nodeValue:' '}, true));
    parent.hidden=false; document.createRange=()=>({selectNodeContents:()=>{},getClientRects:()=>[{...rect,left:2000,right:2100}],detach:()=>{}});
    results.push(check({parentElement:parent,nodeValue:' '}, true));
    console.log(JSON.stringify(results));
    '''
    proc = subprocess.run(['node', '-e', js], capture_output=True, text=True, check=True, timeout=10)
    assert json.loads(proc.stdout) == [True, False, False, False]


def test_collector_retains_only_separators_between_visible_same_block_words() -> None:
    script = (Path(__file__).parents[1] / 'skills/visual-debug/scripts/runtime-text-sequence-check.sh').read_text()
    loop = script.split('    let pendingSeparator = "";', 1)[1].split('    const entries = [];', 1)[0]
    js = '''
    function collect(nodes) {
      let pendingSeparator = '', activeBlock=null,activeAnchor=null,activeParts=[];
      const root = {}, NodeFilter={SHOW_TEXT:4}, results=[];
      const flush = () => {if(activeParts.length)results.push(activeParts.join(''));activeParts=[];activeBlock=null;activeAnchor=null;};
      const blockFor = p=>p.block;
      const isRenderedText=(n,allow=false)=>!n.hidden && (n.visible || (allow && /^\\s+$/.test(n.nodeValue)));
      const document={createTreeWalker:()=>{let i=-1;return {nextNode:()=>++i<nodes.length,get currentNode(){return nodes[i]}}}};
    ''' + 'let unused = null;' + loop + '''
      return results;
    }
    const word=(nodeValue,block='a')=>({nodeValue,visible:true,parentElement:{block}});
    const space=()=>({nodeValue:' ',visible:false,parentElement:{block:'a'}});
    console.log(JSON.stringify([
      collect([word('hello'),space(),word('world')]),
      collect([word('hello'),word('world')]),
      collect([word('world'),space(),word('hello')]),
      collect([word('hello'),{...space(),hidden:true},word('world')]),
      collect([word('hello'),space(),word('world','b')]),
      collect([space(),word('hello'),space()]),
    ]));
    '''
    proc = subprocess.run(['node', '-e', js], capture_output=True, text=True, check=True, timeout=10)
    assert json.loads(proc.stdout) == [['hello world'], ['helloworld'], ['world hello'],
                                      ['helloworld'], ['hello', 'world'], ['hello']]
