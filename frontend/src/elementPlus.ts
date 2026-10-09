import type { App, Plugin } from "vue";
import {
  ElAlert,
  ElButton,
  ElCheckbox,
  ElCollapse,
  ElDatePicker,
  ElDescriptions,
  ElDialog,
  ElDrawer,
  ElDropdown,
  ElEmpty,
  ElForm,
  ElIcon,
  ElInput,
  ElInputNumber,
  ElLoading,
  ElPagination,
  ElPopconfirm,
  ElProgress,
  ElRadio,
  ElSelect,
  ElSkeleton,
  ElSwitch,
  ElTable,
  ElTabs,
  ElTag,
  ElTooltip,
} from "element-plus";

/*
 * Element Plus components used by the admin console, registered one by one so
 * the bundle only carries what templates use (instead of `app.use(ElementPlus)`).
 * Parent installs also register their children: ElTable → ElTableColumn,
 * ElSelect → ElOption, ElTabs → ElTabPane, ElForm → ElFormItem, ElDescriptions
 * → ElDescriptionsItem, ElCollapse → ElCollapseItem, ElRadio → ElRadioGroup /
 * ElRadioButton, ElDropdown → ElDropdownMenu / ElDropdownItem.
 * ElLoading provides the v-loading directive. ElMessage, ElMessageBox,
 * ElNotification and ElImageViewer are imported where they are used.
 *
 * tests/elementPlusRegistry.spec.ts fails when a template uses an <el-*> tag
 * that is not covered here; add the component (or its parent) to this list.
 */
export const ELEMENT_PLUS_PLUGINS: Plugin[] = [
  ElAlert,
  ElButton,
  ElCheckbox,
  ElCollapse,
  ElDatePicker,
  ElDescriptions,
  ElDialog,
  ElDrawer,
  ElDropdown,
  ElEmpty,
  ElForm,
  ElIcon,
  ElInput,
  ElInputNumber,
  ElLoading,
  ElPagination,
  ElPopconfirm,
  ElProgress,
  ElRadio,
  ElSelect,
  ElSkeleton,
  ElSwitch,
  ElTable,
  ElTabs,
  ElTag,
  ElTooltip,
];

export function installElementPlus(app: App): void {
  for (const plugin of ELEMENT_PLUS_PLUGINS) app.use(plugin);
}
