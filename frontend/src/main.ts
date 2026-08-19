import "element-plus/dist/index.css";

import ElementPlus from "element-plus";
import { createApp } from "vue";

import App from "./App.vue";
import "./styles/base.css";

createApp(App).use(ElementPlus).mount("#app");
