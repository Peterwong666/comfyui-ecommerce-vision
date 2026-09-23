import {
  AppstoreOutlined,
  BarChartOutlined,
  ExperimentOutlined,
  PictureOutlined,
  ProfileOutlined,
  SettingOutlined,
  ThunderboltOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons'
import { Layout, Menu, Space, Tag, Typography } from 'antd'
import { Link, Outlet, useLocation } from 'react-router-dom'

import { useMe } from '../api/hooks'
import { useAuthStore } from '../stores/authStore'

const { Header, Sider, Content } = Layout

/**
 * 全局框架：**顶栏 56px + 左侧栏 200px + 主内容区 maxWidth 1280 居中**。
 *
 * 尺寸取自线框图 §0.1。用「左侧栏 + 顶栏」而非纯顶栏的理由也是那条：
 * 任务中心与画廊都要按状态筛选，侧栏能承载筛选器，避免每次开弹窗。
 *
 * V1 只做桌面端（`todolist.md` 明确把移动端列入不做清单）。
 */

interface NavItem {
  key: string
  label: string
  icon: React.ReactNode
}

const NAV_ITEMS: NavItem[] = [
  { key: '/', label: '工作台', icon: <ThunderboltOutlined /> },
  { key: '/batch', label: '批量生成', icon: <UnorderedListOutlined /> },
  { key: '/tasks', label: '任务中心', icon: <ProfileOutlined /> },
  { key: '/gallery', label: '画廊', icon: <PictureOutlined /> },
  { key: '/templates', label: '模板库', icon: <AppstoreOutlined /> },
  { key: '/quality', label: '质量看板', icon: <BarChartOutlined /> },
  { key: '/compare', label: 'A/B 对比', icon: <ExperimentOutlined /> },
  { key: '/admin', label: '管理后台', icon: <SettingOutlined /> },
]

export function AppShell() {
  const location = useLocation()
  const user = useAuthStore((s) => s.user)
  const token = useAuthStore((s) => s.token)
  const me = useMe({ enabled: Boolean(token) })

  const quota = me.data ?? user

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header
        style={{
          height: 56,
          lineHeight: '56px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          paddingInline: 20,
          background: '#001529',
        }}
      >
        <Typography.Text strong style={{ color: '#fff', fontSize: 16 }}>
          商品图量产
        </Typography.Text>

        <Space size="middle">
          {quota && (
            <Tag color={quota.quota_remaining > 0 ? 'blue' : 'red'}>
              额度 {quota.quota_remaining}/{quota.quota_total}
            </Tag>
          )}
          <Typography.Text style={{ color: 'rgba(255,255,255,0.75)' }}>
            {quota?.email ?? '未登录'}
          </Typography.Text>
        </Space>
      </Header>

      <Layout>
        <Sider width={200} theme="light">
          <Menu
            mode="inline"
            selectedKeys={[location.pathname]}
            style={{ height: '100%', borderInlineEnd: 0 }}
            items={NAV_ITEMS.map((item) => ({
              key: item.key,
              icon: item.icon,
              label: <Link to={item.key}>{item.label}</Link>,
            }))}
          />
        </Sider>

        <Content style={{ padding: 24 }}>
          <div style={{ maxWidth: 1280, margin: '0 auto' }}>
            <Outlet />
          </div>
        </Content>
      </Layout>
    </Layout>
  )
}
