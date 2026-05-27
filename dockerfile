# Pre-download Cartopy Natural Earth shapefiles
RUN python -c " \
import cartopy.io.shapereader as shpreader; \
shpreader.natural_earth(resolution='10m', category='cultural', name='admin_2_counties'); \
shpreader.natural_earth(resolution='50m', category='cultural', name='admin_1_states_provinces_lines'); \
shpreader.natural_earth(resolution='10m', category='cultural', name='populated_places'); \
print('Cartopy shapefiles downloaded.') \
"