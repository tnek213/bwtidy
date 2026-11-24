import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import { ColorModeProvider } from './theme/colorMode.tsx'
import { LayoutProvider } from './theme/layout.tsx'

const queryClient = new QueryClient()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <LayoutProvider>
        <ColorModeProvider>
          <App />
        </ColorModeProvider>
      </LayoutProvider>
    </QueryClientProvider>
  </StrictMode>,
)
