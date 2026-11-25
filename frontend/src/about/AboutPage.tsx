import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Paper,
  Stack,
  SvgIcon,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material'
import type { ReactNode } from 'react'
import Prose, { PROSE_WIDTH } from '../theme/Prose.tsx'
import { useVersion } from '../api/version.ts'
import { type Credit, RUNTIME_CREDITS, TOOL_CREDITS } from './credits.ts'
import External from './External.tsx'
import { LICENSE_URL, SOURCE_URL, WORKS_FOR_ME_URL } from './links.ts'
import thirdParty from './thirdParty.json'


function ExpandIcon() {
  return (
    <SvgIcon>
      <path d="M16.59 8.59 12 13.17 7.41 8.59 6 10l6 6 6-6z" />
    </SvgIcon>
  )
}

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <Stack component="section" aria-labelledby={id} spacing={1.5}>
      <Typography id={id} variant="h6" component="h3">
        {title}
      </Typography>
      {children}
    </Stack>
  )
}

function CreditList({ id, title, credits }: { id: string; title: string; credits: Credit[] }) {
  return (
    <Box>
      <Typography id={id} variant="subtitle2" component="h4">
        {title}
      </Typography>
      <Box component="ul" aria-labelledby={id} sx={{ maxWidth: PROSE_WIDTH, mt: 0.5, mb: 0, pl: 3 }}>
        {credits.map((credit) => (
          <Prose key={credit.name} component="li" sx={{ mb: 0.5 }}>
            <External href={credit.url}>{credit.name}</External> ({credit.language}, {credit.license}):{' '}
            {credit.usedFor}.
          </Prose>
        ))}
      </Box>
    </Box>
  )
}

// The About page (docs/spec.md, About): the version, the license, the source and the thanks.
export default function AboutPage() {
  const version = useVersion()

  return (
    <Box sx={{ p: 2, overflow: 'auto' }}>
      <Typography variant="h5" component="h2" gutterBottom>
        About bwtidy
      </Typography>
      <Stack spacing={4}>
        {version !== undefined && (
          // the docs screenshots hide it (backend/tests/support/docs_shots.py), it changes with every commit
          <Prose data-shot-hide>Version {version}</Prose>
        )}
        <Section id="about-license" title="License">
          <Prose>
            Copyright (C) 2026 Kent Larsson. bwtidy is free software under the GNU Affero General Public License,
            version 3 only. The full text is in <External href={LICENSE_URL}>LICENSE</External>.
          </Prose>
          <Prose>
            bwtidy is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY, without even the implied
            warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License for
            more details.
          </Prose>
          <Prose>
            <External href={SOURCE_URL}>Source code</External>: everyone who uses bwtidy may read it, change it and share
            it under the same license.
          </Prose>
        </Section>
        <Section id="about-works-for-me" title="Does bwtidy work for you?">
          <Prose>
            A thumbs up in <External href={WORKS_FOR_ME_URL}>Works for me</External> on GitHub is very useful. If you
            have a minute, a comment with your bwtidy version (shown above), Bitwarden or Vaultwarden, and roughly how
            many items your vault has helps others decide. Only what you post there is shared, bwtidy itself sends
            nothing.
          </Prose>
        </Section>
        <Section id="about-thanks" title="Thanks">
          <Prose>
            bwtidy stands on the work of many people who share their code freely. Thank you to all of them, and especially
            to these projects.
          </Prose>
          <CreditList id="about-runtime" title="What bwtidy runs on" credits={RUNTIME_CREDITS} />
          <CreditList id="about-tools" title="What bwtidy is built and tested with" credits={TOOL_CREDITS} />
        </Section>
        <Section id="about-packages" title="Every package">
          <Prose>
            The list below holds every package bwtidy itself ships, from its lock files. The image also carries the
            Bitwarden CLI with the packages it brings along, which Bitwarden, Inc. licenses under its own terms (GPL-3.0).
          </Prose>
          <Accordion variant="outlined" disableGutters sx={{ maxWidth: 900 }}>
            <AccordionSummary expandIcon={<ExpandIcon />} aria-controls="about-packages-table" id="about-packages-summary">
              Every package and its license ({thirdParty.length})
            </AccordionSummary>
            <AccordionDetails id="about-packages-table">
              <TableContainer component={Paper} variant="outlined">
                <Table size="small" aria-label="Every package and its license">
                  <TableHead>
                    <TableRow>
                      <TableCell>Package</TableCell>
                      <TableCell>Version</TableCell>
                      <TableCell>From</TableCell>
                      <TableCell>License</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {thirdParty.map((entry) => (
                      <TableRow key={`${entry.ecosystem} ${entry.name} ${entry.version}`}>
                        <TableCell>{entry.name}</TableCell>
                        <TableCell>{entry.version}</TableCell>
                        <TableCell>{entry.ecosystem}</TableCell>
                        <TableCell>{entry.license}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            </AccordionDetails>
          </Accordion>
        </Section>
      </Stack>
    </Box>
  )
}
