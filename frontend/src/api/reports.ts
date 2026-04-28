// Reports API
import { parseJson, API_BASE } from './index'
import type { ReportItem } from '../types/report'

export async function fetchReports(): Promise<ReportItem[]> {
  const res = await fetch(`${API_BASE}/api/reports`)
  return parseJson<ReportItem[]>(res)
}

export async function generateReport(payload: { title?: string; article_ids?: number[]; prompt?: string }): Promise<ReportItem> {
  const res = await fetch(`${API_BASE}/api/reports/generate`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  })
  return parseJson<ReportItem>(res)
}

export async function deleteReport(id: number): Promise<void> {
  const res = await fetch(`${API_BASE}/api/reports/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('删除失败')
}

export async function getReportContent(id: number): Promise<{ report_id: number; content: string; title: string }> {
  const res = await fetch(`${API_BASE}/api/reports/${id}/content`)
  return parseJson<{ report_id: number; content: string; title: string }>(res)
}

export async function indexReportToKb(reportId: number, kbId: number): Promise<{ indexed: boolean; doc_id: number; kb_id: number; message: string }> {
  const res = await fetch(`${API_BASE}/api/reports/${reportId}/index-to-kb`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kb_id: kbId }),
  })
  return parseJson<{ indexed: boolean; doc_id: number; kb_id: number; message: string }>(res)
}

export function getReportExportUrl(id: number, format: 'markdown' | 'pdf'): string {
  return `${API_BASE}/api/reports/${id}/export/${format}`
}
