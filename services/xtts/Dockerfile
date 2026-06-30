FROM pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime

# XTTS-v2 via coqui-tts. transformers pinnen (>=4.41 voor isin_mps_friendly;
# 4.46.x werkt met XTTS). COQUI_TOS_AGREED accepteert de modellicentie
# (Coqui Public Model License — niet-commercieel; zie projectnotities).
ENV COQUI_TOS_AGREED=1
RUN pip install --no-cache-dir \
      coqui-tts "transformers==4.46.3" "numpy<2" \
      fastapi "uvicorn[standard]"

WORKDIR /app
COPY server.py /app/server.py

EXPOSE 8001
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]
