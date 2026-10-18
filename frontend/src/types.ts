export interface Meta {
  statuses: string[];
  status_labels: Record<string, string>;
  disclaimer: string;
  suggested_prompts: string[];
}

export interface SourceRef {
  title: string;
  url: string;
}

export interface ChatResponse {
  conversation_id: string;
  state: string;
  reply: string;
  suggested_actions: string[];
  request_reference: string | null;
  sources: SourceRef[];
  safety_notice: string[];
  degraded: boolean;
}

export interface SessionInfo {
  username: string;
  csrf_token: string;
  expires_at: string;
}

export interface StaffSummary {
  total: number;
  counts: Record<string, number>;
}

export interface RequestRow {
  request_reference: string;
  created_at: string;
  customer_name: string;
  equipment_type: string;
  problem_summary: string;
  status: string;
}

export interface RequestPage {
  items: RequestRow[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface HistoryItem {
  old_status: string;
  new_status: string;
  changed_by: string;
  changed_at: string;
}

export interface RequestDetail {
  request_reference: string;
  customer_name: string;
  email: string;
  phone: string | null;
  service_area: string;
  equipment_type: string;
  equipment_brand: string | null;
  equipment_model: string | null;
  serial_number: string | null;
  problem_description: string;
  preferred_contact_method: string;
  preferred_appointment_time: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  history: HistoryItem[];
}
