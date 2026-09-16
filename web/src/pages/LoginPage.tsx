import { LockOutlined, MailOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Form, Input, Typography, message } from 'antd'
import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { api } from '../api/client'
import { describeError } from '../api/errors'
import { useAuthStore } from '../stores/authStore'

interface Credentials {
  email: string
  password: string
}

/**
 * 注册 / 登录。
 *
 * 两种模式共用一个表单：字段完全一致，差别只在接口与按钮文案。
 * 注册会**直接返回令牌**（后端注册即送额度，旅程 2 的"免信用卡 + 送额度"），
 * 所以注册成功不需要再登一次。
 */
export function LoginPage() {
  const navigate = useNavigate()
  const setSession = useAuthStore((s) => s.setSession)
  const [mode, setMode] = useState<'login' | 'register'>('login')

  const mutation = useMutation({
    mutationFn: (values: Credentials) =>
      mode === 'login' ? api.auth.login(values) : api.auth.register(values),
    onSuccess: (token) => {
      setSession(token)
      navigate('/', { replace: true })
    },
    onError: (err) => {
      // 登录失败的 401 含义是"邮箱或密码错"，不是会话过期 ——
      // http 层靠 `anonymous` 标记跳过了登出逻辑（见 http.ts）
      void message.error(describeError(err))
    },
  })

  const isRegister = mode === 'register'

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#f5f5f5',
      }}
    >
      <Card style={{ width: 400 }}>
        <Typography.Title level={4} style={{ textAlign: 'center' }}>
          {isRegister ? '注册并免费试用' : '登录'}
        </Typography.Title>
        <Typography.Paragraph type="secondary" style={{ textAlign: 'center' }}>
          {isRegister ? '免信用卡 · 送 50 张额度 · 不用装软件' : '上传商品图，批量产出可用主图'}
        </Typography.Paragraph>

        <Form<Credentials>
          layout="vertical"
          onFinish={(values) => mutation.mutate(values)}
          initialValues={{ email: '', password: '' }}
        >
          <Form.Item
            name="email"
            label="邮箱"
            rules={[{ required: true, type: 'email', message: '请输入有效邮箱' }]}
          >
            <Input
              prefix={<MailOutlined />}
              placeholder="you@example.com"
              autoComplete="username"
            />
          </Form.Item>

          <Form.Item
            name="password"
            label="密码"
            // 后端约束 8–128 位；前端先拦一道，省一次往返
            rules={[
              { required: true, message: '请输入密码' },
              { min: 8, message: '密码至少 8 位' },
            ]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder="至少 8 位"
              autoComplete={isRegister ? 'new-password' : 'current-password'}
            />
          </Form.Item>

          {mutation.isError && (
            <Alert
              type="error"
              showIcon
              style={{ marginBottom: 16 }}
              message={describeError(mutation.error)}
            />
          )}

          <Button type="primary" htmlType="submit" block loading={mutation.isPending}>
            {isRegister ? '注册并免费试用' : '登录'}
          </Button>
        </Form>

        <div style={{ marginTop: 16, textAlign: 'center' }}>
          <Button type="link" onClick={() => setMode(isRegister ? 'login' : 'register')}>
            {isRegister ? '已有账号？去登录' : '没有账号？注册一个'}
          </Button>
        </div>
      </Card>
    </div>
  )
}
