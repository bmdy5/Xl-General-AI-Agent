#!/usr/bin/env python3
"""Debug Stitch MCP: raw subprocess test."""
import asyncio, json, os, subprocess

gcloud = '/Users/xiaofeng/.stitch-mcp/google-cloud-sdk/bin/gcloud'

async def test():
    min_env = {
        'PATH': f'{os.path.dirname(gcloud)}:/usr/bin:/bin',
        'HOME': os.environ.get('HOME', ''),
        'GOOGLE_CLOUD_PROJECT': 'stitch-496215',
        'CLOUDSDK_CONFIG': '/Users/xiaofeng/.stitch-mcp/config',
    }

    r = subprocess.run([gcloud, 'auth', 'application-default', 'print-access-token'],
        capture_output=True, text=True, timeout=10, env=min_env)
    token = r.stdout.strip()
    print(f'TOKEN: {token[:15]}...')
    env['STITCH_ACCESS_TOKEN'] = token

    mcp_env = {**min_env, 'STITCH_ACCESS_TOKEN': token}
    proc = await asyncio.create_subprocess_exec(
        'npx', 'stitch-mcp',
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=mcp_env,
    )

    init = json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize',
        'params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'t','version':'1'}}})
    proc.stdin.write(init.encode() + b'\n')
    await proc.stdin.drain()

    await asyncio.sleep(5)

    # Check if process exited
    if proc.returncode is not None:
        print(f'Process exited: code={proc.returncode}')
        stderr = proc.stderr.read()
        print(f'STDERR: {stderr[:500].decode() if hasattr(stderr, "decode") else stderr}')
        return

    # Try reading stdout with different methods
    print('Process still running. Trying to read stdout...')

    # Method 1: readline
    try:
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=3)
        print(f'READLINE: {len(line)} bytes -> {line[:200]}')
    except asyncio.TimeoutError:
        print('READLINE: timeout')

    # Method 2: read whatever is available
    try:
        data = await asyncio.wait_for(proc.stdout.read(1024), timeout=2)
        print(f'READ: {len(data)} bytes -> {data[:200]}')
    except asyncio.TimeoutError:
        print('READ: timeout')

    # Method 3: read stderr
    try:
        stderr = await asyncio.wait_for(proc.stderr.read(2048), timeout=2)
        print(f'STDERR: {len(stderr)} bytes -> {stderr[:300].decode()}')
    except asyncio.TimeoutError:
        print('STDERR: timeout')

    proc.kill()
    await proc.wait()

asyncio.run(test())
