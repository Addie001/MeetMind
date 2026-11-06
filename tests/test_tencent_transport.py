import unittest
from unittest.mock import AsyncMock, Mock, patch
from meetmind import mcp


class TencentTransportTests(unittest.IsolatedAsyncioTestCase):
    async def check_dida_headers(self, auth, expected):
        env={'DIDA_MCP_URL':'https://example.invalid/mcp', 'DIDA_MCP_TRANSPORT':'streamable_http',
             'DIDA_MCP_API_KEY':'test-token', 'DIDA_MCP_BEARER_TOKEN':'', 'DIDA_MCP_AUTH':auth}
        with patch.dict('os.environ',env), patch('meetmind.mcp.httpx.AsyncClient') as factory, patch(
                'meetmind.mcp.streamable_http_client',side_effect=RuntimeError('stop before network')):
            factory.return_value.__aenter__.return_value=AsyncMock()
            with self.assertRaises(RuntimeError):
                await mcp._request('dida','tools/list',{})
            self.assertEqual(factory.call_args.kwargs['headers'],expected)

    async def test_official_dida_uses_bearer(self):
        await self.check_dida_headers('bearer',{'Authorization':'Bearer test-token'})

    async def test_community_dida_can_use_api_key(self):
        await self.check_dida_headers('api_key',{'x-api-key':'test-token'})

    @patch.dict('os.environ', {'TENCENT_MCP_URL':'https://example.invalid/mcp',
                               'TENCENT_MCP_TRANSPORT':'jsonrpc',
                               'TENCENT_MEETING_TOKEN':'test-token',
                               'TENCENT_SKILL_VERSION':'v1.0.15'})
    async def test_official_jsonrpc_injects_headers_and_client_info(self):
        response=Mock()
        response.json.return_value={'jsonrpc':'2.0','id':1,'result':{'content':[]}}
        client=AsyncMock()
        client.post.return_value=response
        with patch('meetmind.mcp.httpx.AsyncClient') as factory:
            factory.return_value.__aenter__.return_value=client
            result=await mcp._request('tencent','tools/call',{'name':'get_meeting','arguments':{'meeting_id':'example'}})
        self.assertEqual(result, {'content':[]})
        self.assertEqual(factory.call_args.kwargs['headers']['X-Tencent-Meeting-Token'],'test-token')
        payload=client.post.call_args.kwargs['json']
        self.assertEqual(payload['method'],'tools/call')
        self.assertEqual(payload['params']['arguments']['meeting_id'],'example')
        self.assertEqual(payload['params']['arguments']['_client_info']['agent'],'meetmind')
        self.assertNotIn('test-token',str(payload))

    @patch.dict('os.environ', {'TENCENT_MCP_URL':'https://example.invalid/mcp',
                               'TENCENT_MCP_TRANSPORT':'jsonrpc', 'TENCENT_MEETING_TOKEN':'test-token'})
    async def test_nested_business_error_is_rejected(self):
        response=Mock()
        response.json.return_value={'result':{'error':{'code':401,'message':'denied'}}}
        client=AsyncMock()
        client.post.return_value=response
        with patch('meetmind.mcp.httpx.AsyncClient') as factory:
            factory.return_value.__aenter__.return_value=client
            with self.assertRaises(ValueError):
                await mcp._request('tencent','tools/list',{})


if __name__ == '__main__':
    unittest.main()
