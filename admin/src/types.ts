export type AdminSnapshot = {
  env_default_model: string;
  env_allowed_models: string[] | null;
  use_env_default: boolean;
  use_env_allowed: boolean;
  effective_default_model: string;
  unrestricted: boolean;
  selected_model_ids: string[];
  catalog_ids: string[];
};

export type AdminForm = {
  useEnvDefault: boolean;
  defaultModel: string;
  useEnvAllowed: boolean;
  unrestricted: boolean;
  allowed: string[];
};

export type AdminSaveBody = {
  use_env_default: boolean;
  use_env_allowed: boolean;
  default_model: string;
  unrestricted: boolean;
  allowed_models: string[];
};
