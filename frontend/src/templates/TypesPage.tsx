import { Box, Tab, Tabs } from '@mui/material'
import { useState } from 'react'
import DatatypesTab from './DatatypesTab.tsx'
import { TemplatesTab } from './TemplatesTab.tsx'

export default function TypesPage({ onStatistics }: { onStatistics: () => void }) {
  const [tab, setTab] = useState<'templates' | 'datatypes'>('templates')
  return (
    <Box sx={{ p: 2, overflow: 'auto' }}>
      <Tabs value={tab} onChange={(_, next: 'templates' | 'datatypes') => setTab(next)} sx={{ mb: 2 }}>
        <Tab value="templates" label="Templates" />
        <Tab value="datatypes" label="Datatypes" />
      </Tabs>
      {tab === 'templates' ? <TemplatesTab /> : <DatatypesTab onStatistics={onStatistics} />}
    </Box>
  )
}
