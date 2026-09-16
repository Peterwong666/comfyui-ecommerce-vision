import js from '@eslint/js'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import globals from 'globals'
import tseslint from 'typescript-eslint'

/**
 * ESLint 扁平配置（CONTRIBUTING.md：前端用 ESLint + Prettier）。
 *
 * ⚠️ 这里**只放前端规则**。仓库里已经有两份 Python 的 ruff 配置
 * （根 `pyproject.toml` 与 `backend/pyproject.toml`），而 ruff 按"最近祖先"解析配置 ——
 * 所以在 `web/` 里**绝不能**再出现 `pyproject.toml` / `ruff.toml` / `mypy.ini`，
 * 那会给仓库的"哪个配置生效"再添一层歧义。Node 侧配置只放这三个文件。
 */
export default tseslint.config(
  // `dist` 是构建产物，不该被 lint
  { ignores: ['dist', 'node_modules', 'coverage', 'src/test/fixtures'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      // 允许以 `_` 前缀显式标记"故意不用"的参数
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
    },
  },
)
