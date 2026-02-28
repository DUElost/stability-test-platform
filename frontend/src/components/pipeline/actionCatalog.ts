/**
 * Built-in action catalog for the Pipeline Editor.
 * Maps action names to their metadata, param schemas, and category groupings.
 */

import type { ParamSchema } from '../task/DynamicToolForm';

export interface ActionDef {
  name: string;
  label: string;
  category: 'device' | 'process' | 'file' | 'log' | 'script';
  description: string;
  paramSchema: ParamSchema;
}

export const ACTION_CATEGORIES: { key: string; label: string }[] = [
  { key: 'device', label: 'Device' },
  { key: 'process', label: 'Process' },
  { key: 'file', label: 'File' },
  { key: 'log', label: 'Log' },
  { key: 'script', label: 'Script' },
];

export const BUILTIN_ACTIONS: ActionDef[] = [
  // --- Device actions ---
  {
    name: 'check_device',
    label: 'Check Device',
    category: 'device',
    description: 'Verify ADB connectivity via adb shell echo test',
    paramSchema: {},
  },
  {
    name: 'ensure_root',
    label: 'Ensure Root',
    category: 'device',
    description: 'Ensure ADB root access with retries',
    paramSchema: {
      max_attempts: { type: 'number', label: 'Max Attempts', default: 3, min: 1, max: 10 },
    },
  },
  {
    name: 'clean_env',
    label: 'Clean Environment',
    category: 'device',
    description: 'Uninstall packages, clear logs, set system properties',
    paramSchema: {
      uninstall_packages: { type: 'string', label: 'Uninstall Packages', placeholder: 'com.app1, com.app2', description: 'Comma-separated package names' },
      clear_logs: { type: 'boolean', label: 'Clear Logs', default: true },
      log_dirs: { type: 'string', label: 'Log Directories', placeholder: '/data/aee_exp, /data/vendor/aee_exp', description: 'Comma-separated directories' },
    },
  },
  {
    name: 'connect_wifi',
    label: 'Connect WiFi',
    category: 'device',
    description: 'Connect device to specified WiFi network',
    paramSchema: {
      ssid: { type: 'string', label: 'SSID', required: true },
      password: { type: 'string', label: 'Password', required: true },
    },
  },
  {
    name: 'install_apk',
    label: 'Install APK',
    category: 'device',
    description: 'Install APK file on device',
    paramSchema: {
      apk_path: { type: 'string', label: 'APK Path', required: true, placeholder: '/path/to/app.apk' },
      reinstall: { type: 'boolean', label: 'Reinstall', default: true },
    },
  },
  {
    name: 'push_resources',
    label: 'Push Resources',
    category: 'device',
    description: 'Push files to device via adb push',
    paramSchema: {
      files: { type: 'string', label: 'Files (JSON)', placeholder: '[{"local":"...","remote":"...","chmod":"755"}]', description: 'JSON array of {local, remote, chmod?}' },
    },
  },
  {
    name: 'fill_storage',
    label: 'Fill Storage',
    category: 'device',
    description: 'Fill device storage to target percentage',
    paramSchema: {
      target_percentage: { type: 'number', label: 'Target %', default: 60, min: 1, max: 99 },
    },
  },

  // --- Process actions ---
  {
    name: 'start_process',
    label: 'Start Process',
    category: 'process',
    description: 'Start command via adb shell, optionally in background',
    paramSchema: {
      command: { type: 'string', label: 'Command', required: true, placeholder: 'monkey -p com.app --throttle 500 100000' },
      background: { type: 'boolean', label: 'Background', default: true },
      timeout: { type: 'number', label: 'Process Timeout (s)', default: 3600, min: 1 },
    },
  },
  {
    name: 'monitor_process',
    label: 'Monitor Process',
    category: 'process',
    description: 'Monitor running process with periodic alive check',
    paramSchema: {
      pid_from_step: { type: 'string', label: 'PID From Step', placeholder: 'start_process step name', description: 'Step name to get PID from' },
      duration: { type: 'number', label: 'Duration (s)', default: 3600, min: 1 },
      check_interval: { type: 'number', label: 'Check Interval (s)', default: 5, min: 1 },
      log_paths: { type: 'string', label: 'Log Paths', placeholder: '/data/aee_exp, /data/vendor/aee_exp', description: 'Comma-separated paths to monitor' },
      pull_on_error: { type: 'boolean', label: 'Pull on Error', default: false },
    },
  },
  {
    name: 'stop_process',
    label: 'Stop Process',
    category: 'process',
    description: 'Kill process by PID via adb shell kill -9',
    paramSchema: {
      pid_from_step: { type: 'string', label: 'PID From Step', placeholder: 'start_process step name' },
    },
  },
  {
    name: 'run_instrument',
    label: 'Run Instrument',
    category: 'process',
    description: 'Run Android instrumentation test',
    paramSchema: {
      runner: { type: 'string', label: 'Runner', required: true, placeholder: 'com.app.test/androidx.test.runner.AndroidJUnitRunner' },
      instrument_args: { type: 'string', label: 'Args (JSON)', placeholder: '{"class":"com.app.Test"}', description: 'JSON object of instrument arguments' },
      timeout: { type: 'number', label: 'Timeout (s)', default: 3600, min: 1 },
    },
  },

  // --- File actions ---
  {
    name: 'adb_pull',
    label: 'ADB Pull',
    category: 'file',
    description: 'Pull file/directory from device to local path',
    paramSchema: {
      remote_path: { type: 'string', label: 'Remote Path', required: true, placeholder: '/data/logs' },
      local_path: { type: 'string', label: 'Local Path', required: true, placeholder: '{log_dir}/logs' },
    },
  },
  {
    name: 'collect_bugreport',
    label: 'Collect Bugreport',
    category: 'file',
    description: 'Generate and pull Android bugreport',
    paramSchema: {
      remote_path: { type: 'string', label: 'Remote Path', placeholder: '/sdcard/bugreport.txt' },
      local_dir: { type: 'string', label: 'Local Directory', required: true, placeholder: '{log_dir}' },
    },
  },
  {
    name: 'scan_aee',
    label: 'Scan AEE',
    category: 'file',
    description: 'Scan and pull AEE exception directories',
    paramSchema: {
      aee_dirs: { type: 'string', label: 'AEE Dirs', placeholder: '/data/aee_exp, /data/vendor/aee_exp', description: 'Comma-separated AEE directories' },
      local_dir: { type: 'string', label: 'Local Directory', required: true, placeholder: '{log_dir}/aee' },
    },
  },

  // --- Log actions ---
  {
    name: 'aee_extract',
    label: 'AEE Extract',
    category: 'log',
    description: 'Decrypt AEE DB logs using aee_extract tool',
    paramSchema: {
      input_dir: { type: 'string', label: 'Input Dir', required: true, placeholder: '{log_dir}/aee' },
      output_dir: { type: 'string', label: 'Output Dir', required: true, placeholder: '{log_dir}/aee_decoded' },
      tool_path: { type: 'string', label: 'Tool Path', placeholder: 'Auto-detected if empty' },
    },
  },
  {
    name: 'log_scan',
    label: 'Log Scan',
    category: 'log',
    description: 'Scan log files for keywords, generate report',
    paramSchema: {
      input_dir: { type: 'string', label: 'Input Dir', required: true, placeholder: '{log_dir}' },
      keywords: { type: 'string', label: 'Keywords', default: 'FATAL, CRASH, ANR', description: 'Comma-separated keywords' },
      deduplicate: { type: 'boolean', label: 'Deduplicate', default: true },
    },
  },

  // --- Script adapter ---
  {
    name: 'run_tool_script',
    label: 'Run Tool Script',
    category: 'script',
    description: 'Execute a tool class as a pipeline step',
    paramSchema: {
      script_path: { type: 'string', label: 'Script Path', required: true, placeholder: 'C:\\path\\to\\tool.py or /path/to/tool.py' },
      script_class: { type: 'string', label: 'Class Name', required: true, placeholder: 'MyToolClass' },
      default_params: { type: 'string', label: 'Default Params (JSON)', placeholder: '{}', description: 'JSON object for default params' },
    },
  },
];

/** Lookup action def by name */
export function getActionDef(name: string): ActionDef | undefined {
  return BUILTIN_ACTIONS.find((a) => a.name === name);
}

/** Group actions by category */
export function getActionsByCategory(): Map<string, ActionDef[]> {
  const map = new Map<string, ActionDef[]>();
  for (const cat of ACTION_CATEGORIES) {
    map.set(cat.key, BUILTIN_ACTIONS.filter((a) => a.category === cat.key));
  }
  return map;
}
