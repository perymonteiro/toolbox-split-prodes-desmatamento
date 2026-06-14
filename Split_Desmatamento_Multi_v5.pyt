
# -*- coding: utf-8 -*-
import arcpy
import os
import re
import uuid

RECORTES = [
    "Cerrado",
    "Amazônia",
    "Caatinga",
    "Mata Atlântica",
    "Pampa",
    "Pantanal",
    "Amazônia Legal"
]

class Toolbox(object):
    def __init__(self):
        self.label = "Split Desmatamento por Ano (multi-entrada)"
        self.alias = "split_desmatamento_multi"
        self.tools = [SplitDesmatamentoPorAno_v5]


class SplitDesmatamentoPorAno_v5(object):
    def __init__(self):
        self.label = "Gerar FCs por Ano (camadas abertas, dAAAA) — V5 (normalização + validação)"
        self.description = (
            "Processa múltiplas camadas vetoriais abertas (GPFeatureLayer). Normaliza o campo 'class_name' em memória "
            "(remove espaços, padroniza minúsculas e recorta para 5 chars) em um campo auxiliar '__cnorm', sem alterar o dado fonte. "
            "Seleciona por igualdade (__cnorm = 'dAAAA') e exporta uma FC por ano. Ao final, valida cada saída para conter "
            "apenas o 'class_name' correspondente."
        )
        self.canRunInBackground = True

    def getParameterInfo(self):
        p_table = arcpy.Parameter(
            displayName="Entradas (uma linha por camada)",
            name="inputs_table",
            datatype="GPValueTable",
            parameterType="Required",
            direction="Input"
        )
        p_table.columns = [["GPFeatureLayer", "Camada (Layer)"], ["GPString", "Recorte geográfico"]]
        try:
            p_table.filters[1].type = "ValueList"
            p_table.filters[1].list = RECORTES[:]
        except Exception:
            pass

        p_gdb = arcpy.Parameter(
            displayName="File Geodatabase de saída (*.gdb)",
            name="out_gdb",
            datatype="DEWorkspace",
            parameterType="Required",
            direction="Input"
        )
        p_gdb.filter.list = ["Local Database"]

        p_fd = arcpy.Parameter(
            displayName="Feature Dataset de saída",
            name="out_feature_dataset",
            datatype="GPString",
            parameterType="Required",
            direction="Input"
        )

        p_ano_ini = arcpy.Parameter(displayName="Ano inicial (opcional)", name="ano_inicial", datatype="GPLong", parameterType="Optional", direction="Input")
        p_ano_fim = arcpy.Parameter(displayName="Ano final (opcional)", name="ano_final", datatype="GPLong", parameterType="Optional", direction="Input")

        p_ow = arcpy.Parameter(displayName="Sobrescrever outputs existentes?", name="overwrite_outputs", datatype="GPBoolean", parameterType="Optional", direction="Input")
        p_ow.value = True

        # 6) Validação final estrita (re-filtra a saída se necessário)
        p_validate = arcpy.Parameter(
            displayName="Validar saídas e re-filtrar se necessário (recomendado)",
            name="strict_validation",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input"
        )
        p_validate.value = True

        return [p_table, p_gdb, p_fd, p_ano_ini, p_ano_fim, p_ow, p_validate]

    def updateParameters(self, parameters):
        out_gdb = parameters[1].valueAsText
        if out_gdb and arcpy.Exists(out_gdb):
            arcpy.env.workspace = out_gdb
            fds = arcpy.ListDatasets(feature_type="feature") or []
            parameters[2].filter.type = "ValueList"
            parameters[2].filter.list = sorted(fds)
        vt = parameters[0]
        try:
            vt.filters[1].type = "ValueList"
            vt.filters[1].list = RECORTES[:]
        except Exception:
            pass
        return

    # ----------------- Helpers -----------------
    def _prefix(self, rec):
        return {
            "Cerrado": "cerr_",
            "Amazônia": "amaz_",
            "Caatinga": "caat_",
            "Mata Atlântica": "mata_",
            "Pampa": "pamp_",
            "Pantanal": "pant_",
            "Amazônia Legal": "amzL_"
        }.get(rec)

    def _resolve_layer_or_path(self, val):
        if not val:
            return None
        try:
            if arcpy.Exists(val):
                d = arcpy.Describe(val)
                cp = getattr(d, 'catalogPath', None)
                if cp:
                    return cp
        except Exception:
            pass
        return val

    def _read_vt_rows(self, vt_param):
        rows = []
        raw = vt_param.value
        if raw is None:
            return rows
        if hasattr(raw, 'rowCount') and hasattr(raw, 'getRow'):
            for i in range(raw.rowCount):
                r = raw.getRow(i)
                rows.append([r[0], r[1] if len(r) > 1 else None])
            return rows
        if isinstance(raw, (list, tuple)):
            for r in raw:
                if isinstance(r, (list, tuple)):
                    rows.append([r[0], r[1] if len(r) > 1 else None])
            return rows
        txt = vt_param.valueAsText
        if txt:
            for line in txt.split(';'):
                line = line.strip()
                if not line:
                    continue
                if '|' in line:
                    cols = [c.strip() for c in line.split('|')]
                else:
                    cols = [c.strip() for c in line.split(' ')]
                while len(cols) < 2:
                    cols.append('')
                rows.append([cols[0], cols[1]])
        return rows

    def _normalize_to_memory(self, src_fc):
        """Copia src_fc para in_memory, cria campo __cnorm e calcula valor normalizado de class_name.
        Normalização: (class_name or '').strip().lower()[:5]
        Retorna caminho da FC normalizada.
        """
        tmp_copy = os.path.join("in_memory", f"norm_{uuid.uuid4().hex[:10]}")
        arcpy.management.CopyFeatures(src_fc, tmp_copy)
        # Garante existência do campo
        if '__cnorm' not in [f.name for f in arcpy.ListFields(tmp_copy)]:
            arcpy.management.AddField(tmp_copy, '__cnorm', 'TEXT', field_length=5)
        # Calcula (Python3)
        arcpy.management.CalculateField(
            tmp_copy,
            '__cnorm',
            "(!class_name! if !class_name! is not None else '').strip().lower()[:5]",
            expression_type='PYTHON3'
        )
        return tmp_copy

    def execute(self, parameters, messages):
        vt_param = parameters[0]
        out_gdb = parameters[1].valueAsText
        fd_name = parameters[2].valueAsText
        ano_ini = parameters[3].value
        ano_fim = parameters[4].value
        overwrite = bool(parameters[5].value)
        strict_validation = bool(parameters[6].value)

        arcpy.env.overwriteOutput = overwrite
        arcpy.env.qualifiedFieldNames = False  # evita confusões com nomes qualificados

        rows = self._read_vt_rows(vt_param)
        if not rows:
            raise arcpy.ExecuteError("Adicione pelo menos uma linha com a camada e o recorte.")

        fd_path = os.path.join(out_gdb, fd_name)
        fd_exists_before = arcpy.Exists(fd_path)
        fd_sr = None

        # Validar e resolver entradas
        inputs = []
        for i, r in enumerate(rows, start=1):
            layer_val = (str(r[0]) if r[0] is not None else '').strip()
            recorte = (str(r[1]) if r[1] is not None else '').strip()

            if recorte not in RECORTES:
                raise arcpy.ExecuteError(f"Linha {i}: recorte inválido. Selecione um valor da lista.")
            if not layer_val:
                raise arcpy.ExecuteError(f"Linha {i}: informe a camada (Layer).")

            ds_path = self._resolve_layer_or_path(layer_val)
            if not (str(ds_path).lower().endswith('.shp') and os.path.isfile(ds_path)) and not arcpy.Exists(ds_path):
                raise arcpy.ExecuteError(f"Linha {i}: camada não encontrada: {layer_val}")

            inputs.append((ds_path, recorte))

        # SR do FD
        if not fd_exists_before:
            for ds_path, _ in inputs:
                try:
                    desc = arcpy.Describe(ds_path)
                    fd_sr = desc.spatialReference
                    if fd_sr and fd_sr.name:
                        break
                except Exception:
                    continue
            if fd_sr is None:
                raise arcpy.ExecuteError("Não foi possível determinar a referência espacial para criar o Feature Dataset.")
            messages.addMessage(f"Criando Feature Dataset '{fd_name}' em {out_gdb} (SR: {fd_sr.name})")
            arcpy.management.CreateFeatureDataset(out_gdb, fd_name, fd_sr)
        else:
            fd_sr = arcpy.Describe(fd_path).spatialReference
            messages.addMessage(f"Usando Feature Dataset existente: {fd_path} (SR: {fd_sr.name})")

        initialized_outputs = set()
        outputs_touched = set()

        # Processamento por entrada
        for idx, (src_fc, recorte) in enumerate(inputs, start=1):
            messages.addMessage(f"[{idx}/{len(inputs)}] {src_fc} | Recorte: {recorte}")

            if not (str(src_fc).lower().endswith('.shp') and os.path.isfile(src_fc)) and not arcpy.Exists(src_fc):
                messages.addWarningMessage(f"Ignorando (não encontrado): {src_fc}")
                continue

            fields = [f.name for f in arcpy.ListFields(src_fc)]
            if 'class_name' not in fields:
                messages.addWarningMessage(f"Sem 'class_name': {src_fc} — ignorado.")
                continue

            # Normaliza em memória
            norm_fc = self._normalize_to_memory(src_fc)

            # Descobrir tags via __cnorm
            tags = set()
            with arcpy.da.SearchCursor(norm_fc, ['__cnorm']) as cur:
                for (v,) in cur:
                    if v and re.match(r'^d\d{4}$', v):
                        tags.add(v)
            if not tags:
                messages.addWarningMessage(f"Sem tags válidas 'dAAAA' após normalização em: {src_fc}")
                arcpy.management.Delete(norm_fc)
                continue

            years_available = sorted({int(t[1:]) for t in tags})
            min_year, max_year = years_available[0], years_available[-1]
            eff_ini = int(ano_ini) if ano_ini is not None else min_year
            eff_fim = int(ano_fim) if ano_fim is not None else max_year
            if eff_ini > eff_fim:
                arcpy.management.Delete(norm_fc)
                raise arcpy.ExecuteError(f"Ano inicial ({eff_ini}) > ano final ({eff_fim}).")

            tags_filtradas = [f"d{y}" for y in years_available if eff_ini <= y <= eff_fim]
            if not tags_filtradas:
                messages.addWarningMessage(
                    f"{src_fc}: nenhum ano no intervalo {eff_ini}-{eff_fim}. Disponíveis: {min_year}-{max_year}."
                )
                arcpy.management.Delete(norm_fc)
                continue

            # Criar layer sobre a normalizada e selecionar por __cnorm
            lyr = f"lyr_{uuid.uuid4().hex[:10]}"
            arcpy.management.MakeFeatureLayer(norm_fc, lyr)
            fld = arcpy.AddFieldDelimiters(lyr, '__cnorm')

            for tag in tags_filtradas:
                out_name = arcpy.ValidateTableName(f"{self._prefix(recorte)}{tag}", fd_path)
                out_fc = os.path.join(fd_path, out_name)
                where = f"{fld} = '{tag}'"

                arcpy.management.SelectLayerByAttribute(lyr, 'NEW_SELECTION', where)
                sel_count = int(arcpy.management.GetCount(lyr)[0])
                if sel_count == 0:
                    continue

                if arcpy.Exists(out_fc) and not overwrite:
                    messages.addWarningMessage(f"Já existe e overwrite=False, mantendo: {out_fc}")
                    continue

                if overwrite and arcpy.Exists(out_fc) and out_fc not in initialized_outputs:
                    messages.addMessage(f"overwrite=True -> limpando: {out_fc}")
                    arcpy.management.Delete(out_fc)

                # Copia seleção e projeta se necessário
                tmp_sel = os.path.join('in_memory', f"sel_{uuid.uuid4().hex[:10]}")
                arcpy.management.CopyFeatures(lyr, tmp_sel)

                tmp_src = tmp_sel
                try:
                    src_sr = arcpy.Describe(tmp_sel).spatialReference
                    if not src_sr.name or src_sr.name != fd_sr.name:
                        tmp_proj = os.path.join('in_memory', f"proj_{uuid.uuid4().hex[:10]}")
                        arcpy.management.Project(tmp_sel, tmp_proj, fd_sr)
                        arcpy.management.Delete(tmp_sel)
                        tmp_src = tmp_proj
                except Exception:
                    pass

                if not arcpy.Exists(out_fc):
                    messages.addMessage(f"Criando: {out_fc} (tag {tag}) +{sel_count}")
                    arcpy.management.CopyFeatures(tmp_src, out_fc)
                    initialized_outputs.add(out_fc)
                else:
                    messages.addMessage(f"Append: {out_fc} (tag {tag}) +{sel_count}")
                    arcpy.management.Append(inputs=[tmp_src], target=out_fc, schema_type='NO_TEST')

                if arcpy.Exists(tmp_src):
                    arcpy.management.Delete(tmp_src)

                outputs_touched.add((out_fc, tag))

            # limpar temporários dessa entrada
            try:
                arcpy.management.SelectLayerByAttribute(lyr, 'CLEAR_SELECTION')
            except Exception:
                pass
            try:
                arcpy.management.Delete(lyr)
            except Exception:
                pass
            try:
                arcpy.management.Delete(norm_fc)
            except Exception:
                pass

        # Validação final estrita nas saídas geradas
        if strict_validation and outputs_touched:
            uniq = {}
            for out_fc, tag in outputs_touched:
                uniq.setdefault(out_fc, tag)  # mesmo out_fc sempre com mesmo tag
            for out_fc, tag in uniq.items():
                try:
                    # Checa se existe algo fora do tag esperado (comparando por normalização em memória)
                    tmp = os.path.join('in_memory', f"chk_{uuid.uuid4().hex[:10]}")
                    arcpy.management.CopyFeatures(out_fc, tmp)
                    if '__cnorm' not in [f.name for f in arcpy.ListFields(tmp)]:
                        arcpy.management.AddField(tmp, '__cnorm', 'TEXT', field_length=5)
                        arcpy.management.CalculateField(
                            tmp, '__cnorm', "(!class_name! if !class_name! is not None else '').strip().lower()[:5]", 'PYTHON3')
                    fld = arcpy.AddFieldDelimiters(tmp, '__cnorm')
                    where_bad = f"{fld} <> '{tag}'"
                    bad_count = int(arcpy.management.GetCount(arcpy.management.MakeFeatureLayer(tmp, f"ly_{uuid.uuid4().hex[:8]}", where_bad))[0])
                except Exception:
                    bad_count = 0
                if bad_count > 0:
                    # Re-cria filtrado
                    tmp2 = os.path.join('in_memory', f"flt_{uuid.uuid4().hex[:10]}")
                    arcpy.conversion.FeatureClassToFeatureClass(tmp, 'in_memory', os.path.basename(tmp2), f"{fld} = '{tag}'")
                    arcpy.management.Delete(out_fc)
                    arcpy.management.CopyFeatures(tmp2, out_fc)
                    arcpy.management.Delete(tmp2)
                    arcpy.AddMessage(f"Validação: {out_fc} re-filtrado para conter somente {tag} (removidos {bad_count} registros fora do padrão).")
                try:
                    arcpy.management.Delete(tmp)
                except Exception:
                    pass

        arcpy.AddMessage("Concluído (V5 — normalização + validação).")
